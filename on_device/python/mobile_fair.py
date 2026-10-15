"""Android-local bridge for the pinned Firstlight FAIR implementation."""
import json
import hashlib
from pathlib import Path

# BattleEnvV1 only verifies canonical masks/towers for the standard ladder
# layout, so tensorization always uses this mode's timeline.
GAME_MODE_LADDER = 72000006
# The scene itself is driven by the native engine; these only label the UI.
GAME_MODE_TEAM_VS_TEAM = 72000014
# AI-vs-AI uses the ladder game mode unchanged: the rules are identical, only
# both seats are driven by a policy. Reusing the verified ladder timeline keeps
# every model input inside the trained domain with no new pacing gate.
GAME_MODE_AI_DUEL = 72000006
# The V4 policy input contract caps a deck at 2 evolutions, 2 hero cards and 3
# equipped forms in total (tensorizer raises "deck role counts violate the V4
# match contract" otherwise). The engine itself accepts more, so these only
# bound what the model is told.
POLICY_MAX_EVOLUTIONS = 2
POLICY_MAX_HEROES = 2
POLICY_MAX_EQUIPPED_FORMS = 3

class CapturedNative:
    """Receives same-boundary raw snapshots; never creates or steps a native scene."""
    public_card_play_events_from_combat_ring = True
    def __init__(self, capture): self.capture = capture
    def create_native_match(self, match): return self.capture["ordinary"]
    def observe_atomic(self): return self.capture
    def observe(self): return self.capture["ordinary"]

_CARD_NAMES={}

def local_card_name(card_id):
    """Card name used as the key of deck*.cf in a match fixture."""
    if not _CARD_NAMES:
        from native_runner.competitive_data import load_competitive_data
        for row in load_competitive_data("card_support")["supported_cards"]:
            _CARD_NAMES[int(row["card_id"])]=str(row["name"])
    return _CARD_NAMES.get(int(card_id),"")

def policy_form_masks(deck_object):
    """The form masks the V4 policy is allowed to see for one deck.

    deck*.sp[].el is the catalog support mask and is what the engine consumes,
    but the tensorizer reads the same field as the equipped set and rejects a
    deck with more than two evolutions, two hero cards or three forms in total.
    Only deck*.cf records what is really equipped, and that is what the policy
    is told. A mode such as "all evolution" may hand the engine eight
    evolutions; the surplus is dropped here so the match can still start.
    """
    forms={str(k):int(v) for k,v in (deck_object.get("cf") or {}).items() if int(v) in (1,2)}
    masks=[]
    for row in deck_object.get("sp",()):
        form=forms.get(str(local_card_name(row["d"])),0)
        masks.append(1 if form==1 else 2 if form==2 else 0)
    evo=[i for i,m in enumerate(masks) if m==1][:POLICY_MAX_EVOLUTIONS]
    hero=[i for i,m in enumerate(masks) if m==2][:POLICY_MAX_HEROES]
    keep=evo+hero
    if len(keep)>POLICY_MAX_EQUIPPED_FORMS:
        # Heroes are a stronger role, so shed surplus evolutions first.
        keep=sorted(keep,key=lambda i:masks[i])[:POLICY_MAX_EQUIPPED_FORMS]
    allowed=set(keep)
    return tuple(m if i in allowed else 0 for i,m in enumerate(masks))

class MobileFair:
    """One policy seat's view of a match.

    A duel drives both owners from one BattleEnvV1, so `environment` and
    `observations` may be supplied by the first seat; the second seat then only
    builds its own tensorizer bound to its own actor_owner.
    """

    def __init__(self, capture, actor=1, match=None, environment=None, observations=None):
        from native_runner.battle_env import BattleEnvV1
        from native_runner.match_factory import MatchConfig
        from native_runner.training.v4.factory import build_episode_tensorizer_v4,production_semantic_bundle
        from native_runner.timeline import load_game_mode_timeline
        if actor not in (0,1):
            raise ValueError("actor must be native owner 0 or 1")
        bundle=production_semantic_bundle()
        self.native = CapturedNative(capture)
        self.actor = actor
        decks = [tuple(row["cardId"] for row in player["deck"]) for player in capture["ordinary"]["players"]]
        mode_id=int(match["battle"]["gamemode"]) if match else GAME_MODE_LADDER
        # Tower geometry and action masks are fixed by the standard-1v1 map, so
        # every exposed 1v1 mode shares them. Only pacing differs, and that must
        # follow the mode actually running or the model reads the wrong rhythm.
        timeline = load_game_mode_timeline(mode_id)
        self.owns_environment=environment is None
        if self.owns_environment:
            self.environment = BattleEnvV1(native=self.native,
                card_catalog=bundle.native_card_catalog,semantic_subset_contract=bundle.semantic_subset_contract,
                ruleset_id=hashlib.sha256(b"firstlight-local-v1:110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783:e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339").hexdigest(),
                include_native_digest=False,
                mode_timeline=timeline)
        else:
            self.environment = environment
        # deck*.sp[].el is the catalog *support* mask the engine needs, but the
        # V4 tensorizer reads deck{owner}_form_availability as the set of
        # EQUIPPED forms and caps it at 2 evolutions / 2 hero cards / 3 total.
        # Passing the support mask made an ordinary deck fail with "deck role
        # counts violate the V4 match contract" before the first decision: a
        # typical skeleton deck has five evolvable cards. Only the forms the
        # player actually equipped (deck*.cf, keyed by card name) may be
        # reported to the policy; the engine keeps the full support mask.
        availability=[policy_form_masks(match["battle"][f"deck{owner}"]) if match else (0,)*8
                      for owner in (0,1)]
        if match and any(tuple(row["d"] for row in match["battle"][f"deck{owner}"]["sp"])!=decks[owner] for owner in (0,1)):
            raise ValueError("Native deck differs from the configured local match")
        if observations is None:
            observations, info = self.environment.reset(match_config=MatchConfig(
                game_mode=mode_id,
                deck0=decks[0],deck1=decks[1],king_tower_level=11,
                deck0_form_availability=availability[0],deck1_form_availability=availability[1],
                tower_troop0_id=match["battle"]["deck0"]["sc"][0]["d"] if match else 159000000,
                tower_troop1_id=match["battle"]["deck1"]["sc"][0]["d"] if match else 159000000),
                options={"render_mode":"native-render","decision_ticks":5,"event_driven_decisions":False})
        # The second duel seat needs the same post-reset observations without
        # resetting the shared environment a second time.
        self.reset_observations = observations
        self.tensorizer = build_episode_tensorizer_v4(self.environment.episode_config, actor_owner=actor)
        self.tensorizer.reject_unknown_public_semantics = False
        start_elixir = float(timeline.timeline.starting_elixir)
        self.tensorizer.start_episode(observations[actor],initial_elixir={0:start_elixir,1:start_elixir})
        self.observation = observations[actor]

    def observe(self, capture):
        if int(capture["tick"]) != int(self.environment.raw_observation["tick"])+5:
            raise ValueError("Expected contiguous five-tick capture")
        self.native.capture = capture
        observations, *_ = self.environment.step({},record_trace=False,observation_owners=(self.actor,),policy_features_only=True)
        self.observation = observations[self.actor]
        self.batch = self.tensorizer.tensorize(self.observation)
        return self.batch

    def ordered_inputs(self, schema):
        import numpy as np
        result=[]
        for specification in schema["inputs"][:89]:
            value=self.batch
            for name in specification["name"].split("."):
                value=getattr(value,name)
            source=value.detach().cpu().numpy()
            shape=tuple(specification["shape"])
            dtype={"float32":"<f4","int64":"<i8","uint8":"u1"}[specification["dtype"]]
            if source.ndim != len(shape) or any(a>b for a,b in zip(source.shape,shape)):
                raise ValueError(f"Input exceeds ABI: {specification['name']}: {source.shape} -> {shape}")
            padding=-1 if specification["name"]=="active_effects.parent_index" else 0
            target=np.full(shape,padding,dtype=dtype)
            target[tuple(slice(0,n) for n in source.shape)]=source
            result.append(target.tobytes(order="C"))
        return result

    def decode(self, decision):
        from native_runner.training.v4 import tensorizer as backend
        from native_runner.training.v4.tensors import ActionSequenceV4
        from native_runner.training.v4.decoding import decode_action_sequence_v4
        arrays={name:backend.torch.tensor([decision[name]],dtype=backend.torch.long)
                for name in ("gate","micro_action_count","candidate_index","candidate_uid","target_cell","delay_offset_bin")}
        sequence=ActionSequenceV4(**arrays)
        decoded=decode_action_sequence_v4(sequence,self.batch.candidates,row=0,observation=self.observation,
            catalog=self.tensorizer.catalog,deck=self.tensorizer.deck,card_costs=self.tensorizer.card_costs,
            ability_id_by_vocab_id=self.tensorizer.ability_id_by_vocab_id,config=self.tensorizer.config,base_latency_ticks=1)
        self.tensorizer.record_action(sequence,self.batch)
        return decoded

    def exercise_decoding(self):
        import numpy as np
        candidate=self.batch.candidates
        active=int(np.flatnonzero(candidate.mask.numpy()[0])[0])
        target=int(np.flatnonzero(candidate.placement.numpy()[0,active])[0])
        return self.decode({"gate":1,"micro_action_count":1,"candidate_index":[active,-1],
                           "candidate_uid":[int(candidate.uid[0,active]),-1],"target_cell":[target,-1],"delay_offset_bin":[0,-1]})

# Tower troops that random-deck modes may deal. 159000004 (Royal Chef) is
# excluded on purpose: it replaces the king tower, so the native scene exposes
# no tower troop at the standard king cell and BattleEnvV1's tower binding
# fails closed at reset() with "does not contain exactly one tower at
# (9000, 3000) for owner 0". The other three occupy the princess cells the
# verified standard layout expects.
RANDOM_DECK_TOWER_TROOPS = (159000000, 159000001, 159000002)

GAME_MODES = {
    "ladder": {
        "id": 72000006,
        "name": "Ladder",
        "label": "常规天梯",
        "category": "圣水与时间变种",
        "description": "经典排位机制（1x/2x/3x圣水递增）",
    },
    "double_elixir": {
        "id": 72000001,
        "name": "DoubleElixir",
        "label": "双倍圣水",
        "category": "圣水与时间变种",
        "description": "开局双倍圣水，极速攻防",
    },
    "triple_elixir": {
        "id": 72000476,
        "name": "TripleElixir",
        "label": "三倍圣水",
        "category": "圣水与时间变种",
        "description": "全场三倍圣水，超强推线",
    },
    "7x_elixir": {
        "id": 72000493,
        "name": "7xElixir",
        "label": "7倍圣水无限火力",
        "category": "圣水与时间变种",
        "description": "圣水秒满，大费卡牌无限倾泻",
    },
    "sudden_death": {
        "id": 72000483,
        "name": "SuddenDeath",
        "label": "突然死亡模式",
        "category": "圣水与时间变种",
        "description": "双倍圣水，首破一塔立即终局决胜",
    },
    "ramp_up": {
        "id": 72000500,
        "name": "RampUp",
        "label": "疯狂加速模式",
        "category": "圣水与时间变种",
        "description": "圣水随时间 1x → 2x → 3x 极速递增",
    },
    "mirror": {
        "id": 72000489,
        "name": "MirrorMatch",
        "label": "镜像对决模式",
        "category": "卡组与规则变种",
        "description": "双方使用完全相同的卡组与塔楼，纯技术博弈",
    },
    "all_random": {
        "id": 72000491,
        "name": "AllRandom",
        "label": "全随机卡组模式",
        "category": "卡组与规则变种",
        "description": "系统随机发牌对决，考验临场应变",
    },
    "2v2": {
        "id": 72000014,
        "name": "TeamVsTeam",
        "label": "2v2 协同混战",
        "category": "多人协同对战",
        "description": "1名真人+1名AI协同队友 VS 2名AI敌人",
    },
    "2v2_random": {
        "id": 72000014,
        "name": "TeamVsTeamRandom",
        "label": "2v2 全随机乱斗",
        "category": "多人协同对战",
        "description": "四位选手均由系统在全卡池随机发牌与随机防御塔",
    },
    # 活动与竞技场变种 (Event & Arena Variations)
    # Every mode below runs the standard 1v1 map with an ordinary player deck,
    # so only the pacing inputs differ. Their relative elixir multipliers stay
    # inside the ladder's trained range once POLICY_ELIXIR_MULTIPLIER_CAP is
    # applied, and every phase is one the policy saw during training.
    "rage": {
        "id": 72000073,
        "name": "Rage_Ladder",
        "label": "怒火模式",
        "category": "活动与竞技场变种",
        "description": "圣水阶梯更陡（1x → 2.1x → 3x），中后期爆发力惊人",
    },
    "crazy_arena": {
        "id": 72000502,
        "name": "Crazy_Arena",
        "label": "混沌竞技场",
        "category": "活动与竞技场变种",
        "description": "全程高速圣水充能，节奏比天梯快一档的消耗战",
    },
    "elixir_barrel": {
        "id": 72000384,
        "name": "Event_Elixir_Barrels",
        "label": "圣水桶",
        "category": "活动与竞技场变种",
        "description": "开局仅 3 圣水，圣水回充极慢，重策略轻莽撞",
    },
    "chess_board": {
        "id": 72000373,
        "name": "Event_Chess_Board",
        "label": "棋盘主题活动",
        "category": "活动与竞技场变种",
        "description": "双倍转三倍圣水（2x → 3x），开局 3 圣水的慢热对局",
    },
    "crazy_sudden_death": {
        "id": 72000511,
        "name": "Crazy_Arena_SuddenDeath",
        "label": "混沌·决胜",
        "category": "活动与竞技场变种",
        "description": "全程决胜制：任一方先破一塔立即终局，双倍圣水高风险对攻",
    },
    # PvE 肉鸽爬塔：深度爬塔 + 局内三选一强化 + 双Boss终局。
    # 三个副本共用同一套刷怪原语与同一张 8 卡 enemy roster 规则，差异只在波次编排。
    # gameModeId 72000018 对应原生 PvE 时间线。
    "pve_goblin": {
        "id": 72000018,
        "name": "PvE_Goblin",
        "label": "哥布林秘境·极速狂潮",
        "category": "肉鸽爬塔",
        "description": "10 波极速狂潮，杂兵蜂拥群攻，波间三选一强化，第 10 波双狂暴巨魔",
    },
    "pve_undead": {
        "id": 72000018,
        "name": "PvE_Undead",
        "label": "幽冥墓园·不死空袭",
        "category": "肉鸽爬塔",
        "description": "10 波不死空袭，飞空群怪与亡灵召唤，第 10 波巨型骷髅 + 地狱飞龙",
    },
    "pve_heavy": {
        "id": 72000018,
        "name": "PvE_Heavy",
        "label": "重装熔炉·钢铁巨擘",
        "category": "肉鸽爬塔",
        "description": "10 波钢铁推进，重甲高血量单体压线，第 10 波超级皮卡 + 超级超骑",
    },
    # AI 自战不在 GAME_MODES：它是「对战方式」而不是玩法，由 Java 侧的对手
    # 选择列表直接进入，gamemode 仍用天梯。create_match 因此永远看不到
    # ai_duel，避免玩家被一个残留的模式选择困在不可用的流程里。
    "all_evolution": {
        "id": 72000491,
        "name": "AllRandom",
        "label": "全觉醒模式",
        "category": "卡组与规则变种",
        "description": "双方各 8 张卡全部以觉醒形态上场，8 张全部可以变身",
    },
}

# The three roguelike climbs. Kept in one place so the catalog, the create_match
# branch and the runner all agree on exactly which keys are roguelike runs. The
# retired endless/stage/boss schedules are deliberately absent: they are neither
# in the catalog nor creatable, so a stale key cannot become a mode with no
# picker entry behind it.
ROGUELIKE_MODES = ("pve_goblin", "pve_undead", "pve_heavy")
PVE_MODES = ROGUELIKE_MODES


def prewarm():
    from native_runner.training.v4.factory import production_semantic_bundle
    from native_runner.rich_telemetry_adapter import load_default_effect_catalog
    from native_runner.timeline import load_game_mode_timeline
    production_semantic_bundle()
    load_default_effect_catalog()
    # Fail loudly here rather than mid-match: a mode whose timeline cannot be
    # loaded would otherwise crash only after the player starts a battle.
    failed=[]
    for key,mode in GAME_MODES.items():
        if mode["id"]==GAME_MODE_TEAM_VS_TEAM:
            continue
        try:
            load_game_mode_timeline(mode["id"])
        except Exception as error:
            failed.append("%s(%s): %s"%(key,mode["id"],error))
    if failed:
        raise RuntimeError("game mode timelines unavailable: "+"; ".join(failed))
    return "ready"

def create_match(deck_json,ai_id,opponent_json="",game_mode="ladder",teammate_json=""):
    from native_runner.match_factory import MatchConfig
    from native_runner.competitive_data import load_competitive_data
    import random
    import secrets
    support={int(row["card_id"]):row for row in load_competitive_data("card_support")["supported_cards"]}
    def decode(value):
        if isinstance(value,list): value={"cards":value,"forms":[0]*8,"choices":{}}
        cards=tuple(value["cards"])
        masks=tuple(value["forms"])
        if len(cards)!=8 or len(set(cards))!=8 or len(masks)!=8: raise ValueError("Incomplete local deck")
        if any(card not in support or isinstance(mask,bool) or not isinstance(mask,int) or mask<0 or mask&~int(support[card]["form_availability"])
               for card,mask in zip(cards,masks)): raise ValueError("Unsupported local card/form")
        names={support[card]["name"] for card in cards}
        choices=value.get("choices",{})
        if any(name not in names or form not in (1,2) for name,form in choices.items()): raise ValueError("Invalid form choice")
        # The per-card masks above are what actually reaches the native replay
        # (deck*.sp[].el), so every card carrying bit 0 is evolvable in the
        # match. `active` records the single evo/hero slot the stock deck editor
        # would equip; it is advisory and does not limit the masks we send.
        active=value.get("active")
        if active is None:
            active=[(1 if mask&1 else 0) if slot==0 else (2 if mask&2 else 0) if slot==1 else
                    (1 if mask&1 else 2 if mask&2 else 0) if slot==2 else 0
                    for slot,mask in enumerate(masks)]
        if len(active)!=8 or any(form not in (0,1,2) or form and not masks[slot]&(1<<(form-1))
                                 for slot,form in enumerate(active)):
            raise ValueError("Invalid equipped form")
        if value.get("allEvolved"):
            # "All evolution" mode: every card is playable evolved. The masks
            # are already per-card, so this only has to stop the single-slot
            # editor convention from discarding the extra evolutions.
            equipped=tuple(1 if mask&1 else 0 for mask in masks)
            choices={str(support[card]["name"]):1 for card,mask in zip(cards,masks) if mask&1}
            tower=value.get("tower",159000000)
            if isinstance(tower,bool) or not isinstance(tower,int) or tower not in (159000000,159000001,159000002,159000004):
                raise ValueError("Unsupported local tower troop")
            return cards,equipped,choices,tower
        if active[0] not in (0,1) or active[1] not in (0,2) or any(active[3:]):
            raise ValueError("Equipped form is outside a native special slot")
        equipped=tuple((1<<(form-1)) if form else 0 for form in active)
        choices={str(support[card]["name"]):form for card,form in zip(cards,active) if form}
        tower=value.get("tower",159000000)
        if isinstance(tower,bool) or not isinstance(tower,int) or tower not in (159000000,159000001,159000002,159000004):
            raise ValueError("Unsupported local tower troop")
        return cards,equipped,choices,tower

    mode_key = str(game_mode) if game_mode else "ladder"
    if mode_key in GAME_MODES:
        mode_id = GAME_MODES[mode_key]["id"]
    else:
        try:
            mode_id = int(mode_key)
        except (ValueError, TypeError):
            mode_id = 72000006
            mode_key = "ladder"

    player=json.loads(deck_json)

    if mode_key == "all_random":
        all_cards = list(support.keys())
        r_cards0 = random.sample(all_cards, 8)
        r_cards1 = random.sample(all_cards, 8)
        r_forms0 = [int(support[c]["form_availability"]) for c in r_cards0]
        r_forms1 = [int(support[c]["form_availability"]) for c in r_cards1]
        human = decode({"cards": r_cards0, "forms": r_forms0, "tower": random.choice(RANDOM_DECK_TOWER_TROOPS)})
        enemy = decode({"cards": r_cards1, "forms": r_forms1, "tower": random.choice(RANDOM_DECK_TOWER_TROOPS)})
    elif mode_key == "all_evolution":
        # Every card in both decks carries the EvoForm bit, so all eight are
        # playable evolved instead of only the single evo slot.
        evo_cards = [c for c, row in support.items() if int(row["form_availability"]) & 1]
        if len(evo_cards) < 8:
            raise ValueError("Not enough evolution cards for an all-evolution match")
        picked = random.sample(evo_cards, 8)
        # Keep the hero bit where the card has one: it is a second, independent
        # per-card mask and costs nothing to keep.
        evo_masks = [int(support[c]["form_availability"]) for c in picked]
        evo_active = [1 if m & 1 else 0 for m in evo_masks]
        both = {"cards": picked, "forms": evo_masks, "active": evo_active,
                "allEvolved": True, "tower": random.choice(RANDOM_DECK_TOWER_TROOPS)}
        human = decode(both)
        enemy = decode({"cards": picked, "forms": evo_masks,
                        "active": list(evo_active), "allEvolved": True,
                        "tower": random.choice(RANDOM_DECK_TOWER_TROOPS)})
    elif mode_key == "mirror":
        human = decode(player)
        enemy = human
    elif mode_key == "2v2_random":
        all_cards = list(support.keys())
        decks = []
        for _ in range(4):
            r_cards = random.sample(all_cards, 8)
            r_forms = [int(support[c]["form_availability"]) for c in r_cards]
            r_tower = random.choice(RANDOM_DECK_TOWER_TROOPS)
            decks.append(decode({"cards": r_cards, "forms": r_forms, "tower": r_tower}))
        names = ["Local Human", "AI 队友 (Teammate)", "敌方 AI 1", "敌方 AI 2"]
        battle = {
            "gmt": 1, "plt": 2, "t1s": 2, "t2s": 2, "tps": 2,
            "gamemode": 72000014, "hm": False, "lvlcap": 11, "cardlvlmin": 11, "stp": 0,
            "trail": 170000000, "te": -1, "location": 15000020, "arena": 54000001,
            "hbd": []
        }
        for owner, (c, f, ch, tw) in enumerate(decks):
            deck_obj = {
                "hdr": "BD01",
                "sp": [({"d": cid, "el": f[idx]} if f[idx] else {"d": cid}) for idx, cid in enumerate(c)],
                "sc": [{"d": int(tw), "l": 1, "t": 0, "c": 1}]
            }
            if ch: deck_obj["cf"] = ch
            battle[f"deck{owner}"] = deck_obj
            battle[f"avatar{owner}"] = {
                "accountID.lo": owner + 1, "accountID.hi": 0, "expLevel": 11, "name": names[owner],
                "arena": 54000001, "welo": -1, "scr": 0, "lti": 0, "npc": (owner > 0)
            }
            battle["hbd"].append({"em": {"ce": [], "de": []}, "sk": {"os": [], "ss": [], "rnd": False}, "bn": {"bg": 0, "fg": 0}, "kt": 11})
        if isinstance(player, dict) and "cosmetics" in player:
            for field in ("em", "sk", "bn"):
                if field in player["cosmetics"]:
                    battle["hbd"][0][field] = player["cosmetics"][field]
        payload = {"battle": battle, "endTick": 7200, "cmd": [], "evt": [], "rndSeed": secrets.randbelow(2000000000) + 1, "time": -1}
        return json.dumps(payload, separators=(",", ":"))
    elif mode_key in ROGUELIKE_MODES:
        from pve_tower_defense import (DUNGEON_LABELS, PVE_GAME_MODE_ID, deck_for,
                                       waves_for, arena_location)
        # The wave seat's cards are the dungeon's wave table's legal source: the
        # engine has no spawn primitive, so a wave is a normal card deploy
        # resolved by id, and the id must belong to a deck the engine actually
        # holds -- exactly once, or the native resolver refuses it. No policy
        # ever runs on this seat; it is only ever a legal target for the wave
        # scheduler and the draft's reward spells.
        enemy_cards=tuple(deck_for(mode_key))
        enemy=decode({"cards":list(enemy_cards),
                      "forms":[int(support[card]["form_availability"]) for card in enemy_cards]})
        human=decode(player)
        waves=waves_for(mode_key)
        # The ally toggle only changes which arena the run is played on: the
        # single-sided defence and the wave schedule are identical either way.
        ai_ally=bool(isinstance(player,dict) and player.get("aiAlly"))
        payload=MatchConfig(game_mode=PVE_GAME_MODE_ID,
            location=arena_location(ai_ally),
            deck0=human[0],deck1=enemy[0],deck0_form_availability=human[1],deck1_form_availability=enemy[1],
            tower_troop0_id=human[3],tower_troop1_id=enemy[3],
            seed=secrets.randbelow(2000000000)+1,king_tower_level=11,level_cap=11,minimum_card_level=11,
            owner0_name="Local Human",owner1_name="PvE Waves").to_replay_dict()
        if human[2]: payload["battle"]["deck0"]["cf"]=human[2]
        if isinstance(player,dict) and "cosmetics" in player:
            for field in ("em","sk","bn"):
                if field in player["cosmetics"]:
                    payload["battle"]["hbd"][0][field]=player["cosmetics"][field]
        # Ask the native runtime for the single-sided arena: it relocates the
        # enemy half's royal towers past the far edge so the player sees none of
        # them, while leaving them standing so the engine's opposing-tower
        # verdict cannot end the run at the 40-tick mark. No other mode writes
        # this key, so no other mode has its arena rewritten.
        payload["pveTowerless"]=True
        descriptor={"mode":mode_key,"label":DUNGEON_LABELS.get(mode_key,mode_key),
                    "waves":[dict(wave) for wave in waves],
                    "enemyDeck":list(enemy_cards),"enemyOwner":1,"aiAlly":ai_ally}
        payload["roguelike"]=descriptor
        payload["pve"]=dict(descriptor,roguelike=True)
        return json.dumps(payload,separators=(",",":"))
    elif mode_key == "2v2" or mode_id == 72000014:
        from team_bot import TEAMMATE_DECK, ENEMY1_DECK, ENEMY2_DECK
        human = decode(player)
        if teammate_json:
            try:
                teammate = decode(json.loads(teammate_json))
            except Exception:
                teammate = (TEAMMATE_DECK, (0,)*8, {}, human[3])
        else:
            teammate = (TEAMMATE_DECK, (0,)*8, {}, human[3])
        enemy1 = (ENEMY1_DECK, (0,)*8, {}, human[3])
        enemy2 = (ENEMY2_DECK, (0,)*8, {}, human[3])
        if opponent_json:
            try:
                custom = decode(json.loads(opponent_json))
                enemy1 = custom
            except Exception:
                pass
        decks = [human, teammate, enemy1, enemy2]
        names = ["Local Human", "AI 队友 (Teammate)", "敌方 AI 1", "敌方 AI 2"]
        battle = {
            "gmt": 1, "plt": 2, "t1s": 2, "t2s": 2, "tps": 2,
            "gamemode": 72000014, "hm": False, "lvlcap": 11, "cardlvlmin": 11, "stp": 0,
            "trail": 170000000, "te": -1, "location": 15000020, "arena": 54000001,
            "hbd": []
        }
        for owner, (c, f, ch, tw) in enumerate(decks):
            deck_obj = {
                "hdr": "BD01",
                "sp": [({"d": cid, "el": f[idx]} if f[idx] else {"d": cid}) for idx, cid in enumerate(c)],
                "sc": [{"d": int(tw), "l": 1, "t": 0, "c": 1}]
            }
            if ch: deck_obj["cf"] = ch
            battle[f"deck{owner}"] = deck_obj
            battle[f"avatar{owner}"] = {
                "accountID.lo": owner + 1, "accountID.hi": 0, "expLevel": 11, "name": names[owner],
                "arena": 54000001, "welo": -1, "scr": 0, "lti": 0, "npc": (owner > 0)
            }
            battle["hbd"].append({"em": {"ce": [], "de": []}, "sk": {"os": [], "ss": [], "rnd": False}, "bn": {"bg": 0, "fg": 0}, "kt": 11})
        if isinstance(player, dict) and "cosmetics" in player:
            for field in ("em", "sk", "bn"):
                if field in player["cosmetics"]:
                    battle["hbd"][0][field] = player["cosmetics"][field]
        payload = {"battle": battle, "endTick": 7200, "cmd": [], "evt": [], "rndSeed": secrets.randbelow(2000000000) + 1, "time": -1}
        return json.dumps(payload, separators=(",", ":"))
    else:
        human = decode(player)
        if opponent_json:
            enemy = decode(json.loads(opponent_json))
        elif ai_id.startswith("hog26_"):
            hog = (26000021,26000014,26000038,26000030,26000010,27000000,28000000,28000011)
            enemy = decode({"cards": hog, "forms": [int(support[card]["form_availability"]) for card in hog]})
        else:
            enemy = human

    payload=MatchConfig(game_mode=mode_id,
        deck0=human[0],deck1=enemy[0],deck0_form_availability=human[1],deck1_form_availability=enemy[1],
        tower_troop0_id=human[3],tower_troop1_id=enemy[3],
        seed=secrets.randbelow(2000000000)+1,king_tower_level=11,level_cap=11,minimum_card_level=11,
        owner0_name="Local Human",owner1_name=ai_id).to_replay_dict()
    for owner,deck in enumerate((human,enemy)):
        if deck[2]: payload["battle"][f"deck{owner}"]["cf"]=deck[2]
    if isinstance(player,dict) and "cosmetics" in player:
        # Cosmetics belong to the human profile, independently of AI deck copies.
        for field in ("em","sk","bn"):
            if field in player["cosmetics"]:
                payload["battle"]["hbd"][0][field]=player["cosmetics"][field]
    return json.dumps(payload,separators=(",",":"))

class DeviceNative(CapturedNative):
    def __init__(self,generation,runtime):
        self.generation=generation
        self.runtime=runtime
        text=runtime.nextFrame(generation,0)
        if text is None: raise RuntimeError("Initial native frame unavailable")
        super().__init__(json.loads(str(text)))
    def observe(self):
        text=self.runtime.nextFrame(self.generation,int(self.capture["tick"])+5)
        if text is None: raise RuntimeError("Native frame stream ended before result")
        self.capture=json.loads(str(text))
        return self.capture["ordinary"]
    def queue_hand_action_at(self,action,*,execute_tick=None,execute_in_ticks=None,clamp_late=False):
        # A duel drives owner 0 as well, so accept either seat. Route through the
        # per-owner bridge: the native queue needs the true hand slot, which the
        # capture reports as "handIndex" on a compacted array.
        return self.queue_hand_action_at_for_owner(action.owner,action,
                                                    execute_tick=execute_tick,
                                                    execute_in_ticks=execute_in_ticks)
    def queue_hand_action_at_for_owner(self,owner,action,*,execute_tick=None,execute_in_ticks=None):
        observed=int(self.capture["tick"])
        due=execute_tick if execute_tick is not None else observed+int(execute_in_ticks)
        # battle_env hands us a HandAction dataclass; the 2v2 coordinator emits
        # plain dicts. Read both shapes instead of assuming one.
        field=(lambda key: action[key]) if isinstance(action,dict) else (lambda key: getattr(action,key))
        # The capture compacts the hand array and reports the true native slot
        # in "handIndex"; queue the native slot, never the compacted position.
        players = self.capture["ordinary"]["players"]
        player=next((item for item in players if int(item["owner"])==int(owner)), None)
        if player is None: raise RuntimeError("Player owner %d not found in capture" % int(owner))
        # battle_env's HandAction carries the bare slot, but the 2v2 coordinator
        # hands over the whole card entry (it needs the card to pick it), whose
        # native slot lives under "handIndex". Coerce both shapes, or the 2v2
        # path dies on int(dict) and every AI seat silently stops playing.
        hand_index=field("hand_index")
        slot=int(hand_index["handIndex"] if isinstance(hand_index,dict) else hand_index)
        hand_card=next((item for item in player.get("hand", []) if "handIndex" in item and int(item["handIndex"])==slot), None)
        if hand_card is None: raise RuntimeError("Hand slot %d not found for owner %d" % (slot, int(owner)))
        card=int(hand_card["cardId"])
        receipt=json.loads(str(self.runtime.queueActionForOwner(self.generation,observed,due,slot,card,int(field("x")),int(field("y")),False,0,0,owner)))
        if not receipt.get("ok"): raise RuntimeError(receipt)
        return receipt
    def queue_ability_action_at(self,action,*,execute_in_ticks):
        from native_runner.cr_native_env import RunnerError
        observed=int(self.capture["tick"])
        if action.owner not in (0,1): raise ValueError("ability owner must be 0 or 1")
        # Per-owner bridge: queueActionForOwner forwards to the native ability
        # path with the given owner, so owner 0 can cast in a duel.
        receipt=json.loads(str(self.runtime.queueActionForOwner(self.generation,observed,
            observed+execute_in_ticks,-1,0,0,0,True,action.object_index,action.secondary_index,action.owner)))
        if not receipt.get("ok"): raise RunnerError("could not resolve one ready native champion ability source")
        return receipt

def run_2v2_device_match(generation,runtime,native,match):
    from team_bot import Team2v2Coordinator
    from java import jclass
    import time
    log=jclass("android.util.Log")
    coordinator=Team2v2Coordinator()
    start=time.perf_counter()
    decisions=0
    tick=0
    while True:
        try:
            ordinary=native.observe()
        except Exception as stream_end:
            # A drained frame stream is the normal end of a match, but any
            # other failure is a real pipeline error and must not be hidden.
            log.w("FirstlightLocal","2v2 frame stream ended tick=%d: %s"%(tick,stream_end))
            break
        if not ordinary or ordinary.get("ended") or ordinary.get("finalized"):
            break
        tick=int(ordinary.get("tick",0))
        if tick>=50:
            actions=coordinator.step(tick,ordinary)
            for owner,action in actions.items():
                try:
                    native.queue_hand_action_at_for_owner(owner,action,execute_in_ticks=2)
                    decisions+=1
                except Exception as rejected:
                    # Budget/cooldown rejections are normal; log at debug level.
                    log.w("FirstlightLocal","2v2 action rejected owner=%d tick=%d: %s"%(owner,tick,rejected))
    return json.dumps({"mode":"2v2","tick":tick,"decisions":decisions,"seconds":time.perf_counter()-start})

def run_duel_device_match(generation,runtime,native,match_data,schema_json):
    """Drive both owners from one shared environment with two policy seats.

    Each seat owns its own MobileFair (own tensorizer, own actor_owner) and its
    own recurrent session on the Java side, because RecurrentPolicySession is
    single-owner: sharing one would poison it on the second submit. Decisions
    are merged into a single step so both sides act on the same frame.
    """
    from java import jclass
    import time
    log=jclass("android.util.Log")
    schema=json.loads(schema_json)
    seats={}
    # Seat 0 builds the environment and resets it; seat 1 reuses both.
    for owner in (0,1):
        adapter=MobileFair(native.capture,actor=owner,match=match_data,
                           environment=seats[0].environment if owner else None,
                           observations=seats[0].reset_observations if owner else None)
        adapter.native=native
        adapter.environment.native=native
        seats[owner]=adapter
    decisions=[0,0]
    seen_plays=[set(),set()]
    plays=[0,0]
    start=time.perf_counter()
    while not (seats[0].observation.terminal.ended or seats[1].observation.terminal.ended):
        tick=seats[0].observation.tick
        actions={}
        for owner in (0,1):
            adapter=seats[owner]
            adapter.batch=adapter.tensorizer.tensorize(adapter.observation)
            if tick>=90:
                inputs=adapter.ordered_inputs(schema)
                decision=json.loads(str(runtime.inferForOwner(generation,owner,tick,inputs)))
                decoded=adapter.decode(decision)
                actions[owner]=decoded.actions
                decisions[owner]+=1
        observations,*_=seats[0].environment.step(actions,record_trace=False,
                                                 observation_owners=(0,1),policy_features_only=True)
        for owner in (0,1):
            seats[owner].observation=observations[owner]
        for owner in (0,1):
            for event in seats[0].environment.deployment_timing_events(owner):
                if event["kind"]=="card_play" and event["nativeEventId"] not in seen_plays[owner]:
                    seen_plays[owner].add(event["nativeEventId"])
                    plays[owner]+=1
                    runtime.recordNativePlay(generation,owner,event["tick"],event["cardId"])
    return json.dumps({"mode":"ai_duel","tick":seats[0].observation.tick,
                       "decisions":decisions[0]+decisions[1],"decisions_by_owner":decisions,
                       "native_plays":plays,"seconds":time.perf_counter()-start})

# The elixir multiplier a PvE buff has currently granted the player, or 1.0.
#
# The three-pick-one offers a permanent elixir-rate buff, and the specification
# requires it to actually take effect. The wave seat cannot express a rate: a
# card deploy moves a unit onto the board, it cannot change how fast the player's
# bar refills. So the rate is owned here and pushed into the native player object
# once per frame by `pve_elixir_rate` below, which is the only place a rate can
# be applied. 1.0 means "the stock rate", which is every non-PvE match and the
# state between drafts.
_PVE_ELIXIR_RATE = [1.0]


def set_pve_elixir_rate(multiplier):
    """Record the elixir multiplier a PvE buff granted, and return the old one.

    Refuses a multiplier that is not a positive finite number rather than storing
    it: a rate that is not a rate would be multiplied into the player's elixir and
    the run would end with an unplayable bar.
    """
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        return _PVE_ELIXIR_RATE[0]
    if not (0.0 < value <= 4.0):
        return _PVE_ELIXIR_RATE[0]
    previous = _PVE_ELIXIR_RATE[0]
    _PVE_ELIXIR_RATE[0] = value
    return previous


def pve_elixir_rate():
    """The elixir multiplier currently in effect."""
    return _PVE_ELIXIR_RATE[0]


# Single-slot mailbox the Java draft dialog writes its pick into. The dialog runs
# on the UI thread and the match loop runs on the worker thread, so the hand-off
# is a module-level cell rather than a lock: blocking the loop would freeze the
# world while the player reads three options, and the write is a pair of
# attribute stores that a lock would only serialise, not make atomic.
#
# The pair is read as a *tuple snapshot* so a reader can never observe a new
# option against the previous run's generation. Assigning the two fields
# separately would let exactly that happen: the loop would see the new option,
# the stale generation, and discard a pick the player actually made.
_DRAFT_MAILBOX = {"generation": 0, "option": None}


def report_draft_choice(generation, option_id):
    """Called by the Java dialog when the player takes (or times out on) a pick.

    The dialog calls this from the UI thread while `run_pve_device_match` is
    blocked inside `nextFrame`, so it must return immediately and must never
    raise into the UI thread.
    """
    try:
        _DRAFT_MAILBOX["option"] = None if option_id is None else str(option_id)
        _DRAFT_MAILBOX["generation"] = int(generation)
    except (TypeError, ValueError) as rejected:
        from java import jclass
        jclass("android.util.Log").w("FirstlightLocal",
                                     "Roguelike draft choice rejected: %s" % (rejected,))


def _take_draft_choice(generation):
    """Consume a pending pick if it belongs to this match, else None.

    Read as a snapshot tuple, then clear, so a pick that arrives between the read
    and the clear is left for the next call instead of being dropped.
    """
    option, owner = _DRAFT_MAILBOX["option"], _DRAFT_MAILBOX["generation"]
    if option is None:
        return None
    if int(owner or 0) != int(generation):
        # A pick left over from a previous battle must not be applied here.
        _DRAFT_MAILBOX["option"] = None
        return None
    _DRAFT_MAILBOX["option"] = None
    return option


def run_pve_device_match(generation,runtime,native,match_data,schema_json):
    """Run a scripted PvE defence: a human seat, a wave scheduler, no AI opponent.

    Neither seat runs the policy. Owner `pve_player_owner` is played by the human
    through the native touch path, and `enemy_owner` is never given a decision at
    all: its cards are the level's wave table, armed through
    LocalBattleRuntime.scheduleCard, which resolves by card id and fires on the
    exact tick the level names. The loop is a pump, not a player.
    """
    from java import jclass
    import time
    log=jclass("android.util.Log")
    pve=match_data.get("pve") or {}
    waves=pve.get("waves") or []
    enemy_owner=int(pve.get("enemyOwner",1))
    pve_player_owner=0 if enemy_owner==1 else 1
    # The observer is built for the player seat, not MobileFair's default of 1:
    # in a PvE match owner 0 is the human and owner 1 is the wave script. The
    # tensorizer validates that the observation it is handed belongs to the owner
    # it was built for, and the default actor=1 raised "observation owner does not
    # match V4 tensorizer" on the first frame.
    adapter=MobileFair(native.capture,actor=pve_player_owner,match=match_data)
    adapter.tensorizer.reject_unknown_public_semantics = False
    adapter.native=native
    adapter.environment.native=native
    decisions=0
    armed=set()
    rejected=[]
    seen_plays=set()
    plays=[0,0]
    # Stage 1 is an empirical question about the engine (does a monster walk to a
    # tower-less side's player?), so the probe records a (tick,x,y) series for
    # every walking unit and ships it back in the match summary. Tracking is on
    # by default for every PvE match because it is also the on-device evidence
    # that Stage 8 asks for (monsters come from the far side and advance), and it
    # costs one dict append per sampled unit every second of match time. A level
    # can opt out with "probe": {"trackPositions": false}.
    probe=pve.get("probe") or {}
    from pve_probe_tracker import PositionTracker
    from pve_tower_verdict import TowerVerdict, pve_winner, read_player_king
    tracker=PositionTracker(int(probe.get("sampleEveryTicks",20)))
    if not probe.get("trackPositions",True):
        tracker=None
    start=time.perf_counter()
    # The engine ends a match by counting the *opposing* towers, which is the
    # duel rule and the exact inverse of a defence: the player has no enemy
    # tower to destroy, and losing their own king is the only way to lose. The
    # verdict is therefore tracked here and reported instead of the engine's.
    verdict=TowerVerdict()
    lost_tick=None
    last_notified_cycle=-1
    max_king_hp=4008
    # A roguelike run adds the interlude state machine on top of the wave pump:
    # nine three-pick-one drafts between the ten waves, and a buff state the
    # picks accumulate into. A legacy endless schedule has none of this, so the
    # block is inert unless create_match marked the match `roguelike`.
    rogue=match_data.get("roguelike") or {}
    from pve_tower_defense import (DRAFT_TIMEOUT_SECONDS, INTERLUDE_TICKS, WAVE_COUNT,
                                   apply_choice, consume_rewards, draft_options,
                                   effective_elixir_rate, initial_state, live_enemy_troops,
                                   reward_deploys, stars_for)
    # The evolvable pool is the player's own deck restricted to the cards the
    # catalog says can evolve, because a draft may only unlock a form the
    # engine would actually accept. A pick consumes one, so a run of four can
    # grant four distinct evolutions.
    rogue_state=initial_state(rogue.get("mode") or pve.get("mode"))
    # The pick callback arrives on the Java worker thread, so the hand-off is a
    # single-slot mailbox: the dialog writes the id, this loop consumes it. A
    # lock would be wrong here -- the loop must not block while the UI is up.
    draft_choice=[None]
    pending_rewards=[]
    drafted_waves=set()
    generation_counter=[0]
    log.i("FirstlightLocal","PvE mode=%s waves=%d"%(pve.get("label") or pve.get("mode"),len(waves)))
    # An endless table is far longer than any match, so arm lazily: only waves
    # whose tick is still ahead of the world get issued this frame. Arming all of
    # them up front would push tens of thousands of commands at the mailbox,
    # which caps at 16 in flight, and the early waves would be buried.
    upcoming=min(len(waves),4096)
    cursor=0
    # The tick of the last armed spawn, used to keep a settling window after the
    # finale so the run is not declared won while its bosses are still standing.
    last_spawn_tick=0
    # Clear detection. `waves_spawned` counts the spawns of the wave currently in
    # progress and `wave_cleared_tick` is when that wave emptied the field; the
    # interlude is measured from the latter, not from a precomputed tick, so the
    # fifteen seconds really are fifteen seconds of quiet after the last monster
    # dies rather than fifteen seconds that overlap the fight.
    mode_key=rogue.get("mode") or pve.get("mode")
    waves_spawned=0
    wave_in_progress=False
    wave_cleared_tick=None
    interlude_started=False
    interlude_wave=None
    log.i("FirstlightLocal","PvE towerless=%s waves=%d"%(bool(match_data.get("pveTowerless")),len(waves)))
    while not adapter.observation.terminal.ended:
        # The world clock comes from the native capture, never from the policy
        # observation. `adapter.environment.step` can fail on this path (the
        # rich-telemetry merge cursor skips when a wave deploy lands between
        # frames), and when it does the observation is *not* advanced -- so
        # reading the tick from it froze the whole pump: the arming cursor saw a
        # constant tick, the 600-tick arming horizon never opened again, and the
        # run silently stopped spawning after the first few waves. The capture is
        # refilled every frame regardless, so it is the only clock that is always
        # true.
        tick=int(native.capture["tick"])
        # ---- wave-cleared detection and the safe interlude ------------------
        # A wave is cleared when every one of its batches has been armed and the
        # field holds no wave-seat unit any more. That is the only condition that
        # means "the fight is over": not the schedule running out (the last batch
        # of a wave lands several seconds after the first) and not a timer (the
        # specification's fifteen seconds are fifteen seconds of quiet *after* the
        # last monster dies, not fifteen seconds that overlap the fight).
        if rogue:
            enemies=live_enemy_troops(native.capture["ordinary"],enemy_owner)
            # The second half of the condition is load-bearing. Without it the
            # field can read empty *between* two batches of the same wave -- the
            # monsters of batch one die, batch two is still three seconds away --
            # and the wave is declared cleared, its interlude starts, and the
            # draft opens while the wave's own second batch is landing. The device
            # log shows exactly that: "wave 4 cleared tick=1995" and again at 2075
            # and 2155, with batches 2 and 3 arming in between.
            pending_batches=any(int(waves[i]["tick"])>tick
                                for i in range(cursor,upcoming))
            if wave_in_progress and enemies==0 and not pending_batches:
                wave_cleared_tick=tick
                wave_in_progress=False
                interlude_started=False
                log.i("FirstlightLocal","PvE wave %d cleared tick=%d"%(1+len(drafted_waves),tick))
        # The interlude is fifteen seconds long and the three-pick-one runs inside
        # it, so a player who answers at the last moment still does it with the
        # next wave committed to but not yet landed. Opened once per cleared wave.
        if rogue and wave_cleared_tick is not None and not interlude_started:
            if tick>=wave_cleared_tick+INTERLUDE_TICKS:
                interlude_started=True
                wave_cleared_tick=None
                log.i("FirstlightLocal","PvE interlude elapsed tick=%d"%(tick,))
        # ---- the roguelike interlude state machine -------------------------
        # A run offers a three-pick-one draft after each cleared wave but the last.
        # The dialog is raised once per interlude and the pick is collected through
        # the mailbox; the world keeps turning underneath, which is the point of a
        # "safe interlude" -- the player is never asked to act while a wave is
        # already landing on them.
        if rogue and interlude_started and interlude_wave is None:
            wave_index=min(1+len(drafted_waves),WAVE_COUNT-1)
            interlude_wave=wave_index
            drafted_waves.add(wave_index)
            generation_counter[0]+=1
            rogue_state=dict(rogue_state)
            rogue_state["wave"]=wave_index+1
            rogue_state["generation"]=generation_counter[0]
            options=draft_options(rogue_state)
            draft_choice[0]=None
            _DRAFT_MAILBOX["option"]=None
            if options:
                try:
                    runtime.showRoguelikeDraft(generation,wave_index,
                        json.dumps([{"id":o["id"],"title":o["title"],
                                     "description":o["description"],
                                     "icon":o["dimension"]} for o in options],
                                   ensure_ascii=False),DRAFT_TIMEOUT_SECONDS)
                    log.i("FirstlightLocal","PvE draft wave=%d options=%s"%(
                        wave_index,",".join(o["id"] for o in options)))
                except Exception as _e:
                    log.w("FirstlightLocal","PvE draft dialog error: %s"%_e)
            # Wait for the pick. The bound is the dialog's own countdown plus a
            # tick of slack, not a precomputed wave tick: the next wave is
            # scheduled from this clear, so waiting for a fixed tick would either
            # leave the dialog up over a landing wave or force the fallback pick
            # on a player who was still reading the three options.
            deadline=tick+DRAFT_TIMEOUT_SECONDS*20+1
            while draft_choice[0] is None and _DRAFT_MAILBOX["option"] is None:
                if int(native.capture["tick"])>=deadline:
                    break
                try:
                    native.observe()
                except Exception:
                    break
                picked=_take_draft_choice(generation)
                if picked:
                    draft_choice[0]=picked
                    break
                tick=int(native.capture["tick"])
            picked=draft_choice[0] or _take_draft_choice(generation)
            if not picked and options:
                picked=options[0]["id"]
                log.i("FirstlightLocal",
                      "PvE draft wave=%d timed out, auto-picked %s"%(wave_index,picked))
            if picked:
                rogue_state=apply_choice(rogue_state,picked,tick)
                log.i("FirstlightLocal","PvE draft chosen wave=%d option=%s"%(
                    wave_index,picked))
            # Any board-touching buff this pick owes is queued here and armed
            # on the next pass, once the interlude has released the pump. The
            # tick is the *current* one, not the tick the interlude opened at:
            # a player who answers instantly would otherwise schedule the
            # reward into the past and the engine would refuse it.
            pending_rewards.extend(reward_deploys(rogue_state,wave_index+1,tick+1))
            rogue_state=consume_rewards(rogue_state)
            # A rate buff is a state the runtime applies to the player's own
            # elixir object, not something the wave table can express, so it is
            # pushed down as a number the native side owns.
            rate=effective_elixir_rate(rogue_state,tick)
            if rate!=pve_elixir_rate():
                log.i("FirstlightLocal","PvE elixir rate %.2fx -> %.2fx"%(
                    pve_elixir_rate(),rate))
                set_pve_elixir_rate(rate)
            # The interlude is over. The next clear detection pass opens the
            # next one, so a run that loses its king mid-pick never loops here.
            interlude_started=False
            interlude_wave=None
        if pending_rewards:
            for reward in pending_rewards:
                try:
                    # Rewards are scheduled from the world clock, not the stale
                    # interlude tick. The interlude opened at the end of a wave
                    # and the pick-wait loop advanced the world past it, so a
                    # reward tick derived from the interlude's opening tick was
                    # already in the past by the time it was armed --
                    # "execute tick is not in the future", and the buff silently
                    # did nothing. `now + offset` is always in the future.
                    now=int(native.capture["tick"])
                    receipt=json.loads(str(runtime.scheduleCard(enemy_owner,int(reward["card"]),
                                                                int(reward["x"]),int(reward["y"]),
                                                                now+int(reward.get("offset",1)),
                                                                int(reward.get("level",0)))))
                    if not receipt.get("ok"):
                        log.w("FirstlightLocal","PvE reward rejected: %s"%receipt)
                    else:
                        log.i("FirstlightLocal","PvE reward armed card=%d tick=%d level=%d label=%s"%(
                            int(reward["card"]),now+int(reward.get("offset",1)),
                            int(reward.get("level",0)),reward["label"]))
                except Exception as _e:
                    log.w("FirstlightLocal","PvE reward error: %s"%_e)
            pending_rewards=[]
        # Arm a bounded window of upcoming waves. The engine executes on the
        # exact boundary and marks a wave MissedBoundary if it is passed
        # unarmed, so the window has to reach past "now" but does not have to
        # cover the whole table. The cursor only moves forward -- the table is
        # sorted by tick, so restarting it each frame would rescan everything.
        ARM_HORIZON=600
        while cursor<upcoming and int(waves[cursor]["tick"])<tick+ARM_HORIZON:
            index=cursor
            wave=waves[cursor]
            wave_tick=int(wave["tick"])
            if index in armed:
                cursor+=1
                continue
            if wave_tick<=tick:
                # The boundary went by unarmed, so the engine will never fire
                # it. Retiring it here is what stops the loop from re-issuing a
                # wave that can no longer succeed.
                armed.add(index)
                cursor+=1
                rejected.append({"wave":index,"tick":wave_tick,"error":"boundary passed unarmed"})
                log.w("FirstlightLocal","PvE wave missed i=%d tick=%d"%(index,wave_tick))
                continue
            receipt=json.loads(str(runtime.scheduleCard(enemy_owner,int(wave["card"]),
                                                    int(wave["x"]),int(wave["y"]),wave_tick,
                                                    int(wave.get("level",0)))))
            if receipt.get("ok"):
                armed.add(index)
                cursor+=1
                waves_spawned+=1
                last_spawn_tick=wave_tick
                if rogue:
                    # A wave counts as in progress from its first armed spawn, so
                    # the clear check cannot fire against an empty field during
                    # the gap between two batches of the same wave.
                    wave_in_progress=True
                log.i("FirstlightLocal","PvE wave armed i=%d card=%d tick=%d level=%d batch=%d/%d"%(
                    index,int(wave["card"]),wave_tick,int(wave.get("level",0)),
                    int(wave.get("batch",0))+1,int(wave.get("batches",1))))
                cycle=wave.get("cycle",0)
                is_boss=wave.get("is_boss",False)
                if (cycle!=last_notified_cycle or is_boss) and hasattr(runtime,"notifyPveWave"):
                    last_notified_cycle=cycle
                    total_cycles=max(1,waves[-1].get("cycle",0)+1)
                    label=wave.get("label",f"第 {cycle + 1} 波")
                    try:
                        runtime.notifyPveWave(generation,cycle,total_cycles,str(label),bool(is_boss))
                    except Exception:
                        pass
            elif receipt.get("error")=="no live battle":
                # Transient: the world is not stepped yet, so the capture
                # identity has no manager. Leave the wave unarmed and retry on a
                # later frame -- the first frames of a match all report this.
                # Break without advancing cursor so index is retried next frame.
                log.w("FirstlightLocal","PvE wave deferred i=%d tick=%d: no live battle yet"%(index,wave_tick))
                break
            else:
                # A permanent refusal (bad owner, off-arena position, tick not
                # in the future). Retrying cannot help, so retire it rather
                # than re-issuing the same call on every remaining frame.
                armed.add(index)
                cursor+=1
                rejected.append({"wave":index,"tick":wave_tick,"error":receipt.get("error")})
                log.w("FirstlightLocal","PvE wave rejected i=%d tick=%d: %s"%(index,wave_tick,receipt))

        # Apply the elixir-rate buff to the player's own bar. Done every frame
        # rather than once at pick time because the native side owns the bar and
        # recomputes it every tick; a buff that was applied once would be
        # overwritten by the next tick's stock refill and the player would never
        # see a faster bar.
        if pve_elixir_rate()!=1.0:
            try:
                receipt=json.loads(str(runtime.setPveElixirRate(pve_player_owner,
                                                                float(pve_elixir_rate()))))
                if not receipt.get("ok"):
                    log.w("FirstlightLocal","PvE elixir rate rejected: %s"%receipt)
            except Exception as _e:
                log.w("FirstlightLocal","PvE elixir rate error: %s"%_e)

        # Stage 1: the player seat is played by the human, not by the policy.
        # An empty actions dict is not a no-op -- BattleEnvV1 fills the missing
        # owners with a synthesised WAIT and still advances the world, so the
        # wave schedule keeps firing while the screen drives owner 0. Passing a
        # decoded decision here would inject a real ct:86 deploy against owner
        # 0's own hand slot (queueActionForOwner), which is exactly the "AI
        # hijacks the player's cards" defect this mode must not have.
        actions={}
        try:
            observations,*_=adapter.environment.step(actions,record_trace=False,
                                                     observation_owners=(pve_player_owner,),
                                                     policy_features_only=True)
            adapter.observation=observations[pve_player_owner]
        except Exception as _e:
            log.w("FirstlightLocal","PvE step error: %s"%_e)
            try:
                native.observe()
            except Exception:
                pass
        # The defence ends when the player's own king tower falls, so the
        # verdict is re-read every frame from the same capture the probe
        # samples. Reading it after the step keeps the frame consistent: the
        # capture belongs to the world the policy just answered.
        verdict=read_player_king(native.capture["ordinary"],pve_player_owner,verdict)
        if verdict.hp>0:
            max_king_hp=max(max_king_hp,verdict.hp)
        if verdict.lost and lost_tick is None:
            lost_tick=verdict.tick
            log.w("FirstlightLocal","PvE king tower down tick=%d hp=%d"%(
                verdict.tick,verdict.hp))
            break
        if rogue:
            # The run is cleared when the whole table has been armed *and* the
            # field has emptied: the finale's bosses are alive and fighting long
            # after their deploy boundary, so a precomputed "past the last spawn
            # tick" condition would declare victory the instant they landed.
            if (cursor>=len(waves) and not wave_in_progress
                    and live_enemy_troops(native.capture["ordinary"],enemy_owner)==0
                    and tick>=last_spawn_tick+300):
                log.i("FirstlightLocal","PvE run cleared tick=%d waves=%d"%(
                    tick,len(waves)))
                break
        elif pve.get("mode") in ("pve_stage","pve_boss") and cursor>=len(waves) and tick>=waves[-1]["tick"]+300:
            log.i("FirstlightLocal","PvE stage cleared tick=%d waves=%d"%(tick,len(waves)))
            break
        if tracker is not None:
            # Sample the *raw* capture, not the policy-shaped observation: the
            # observation is filtered to the policy's own owner and would drop
            # every enemy unit, which is exactly the half Stage 1 is measuring.
            if tracker.maybe_sample(native.capture["ordinary"]):
                log.i("FirstlightLocal","PvE probe tick=%d tracks=%d"%(
                    int(native.capture["tick"]),len(tracker.tracks)))
        for owner in (0,1):
            for event in adapter.environment.deployment_timing_events(owner):
                if event["kind"]=="card_play" and event["nativeEventId"] not in seen_plays:
                    seen_plays.add(event["nativeEventId"])
                    plays[owner]+=1
                    runtime.recordNativePlay(generation,owner,event["tick"],event["cardId"])
    hp_ratio=max(0.0,float(verdict.hp)/float(max_king_hp)) if verdict.hp>0 else 0.0
    # One star function for every PvE flavour, so the roguelike tiers and the
    # legacy schedules can never disagree about what 3 stars means.
    stars=stars_for(hp_ratio)
    # The summary reports the *world's* final tick, from the capture. The policy
    # observation can be behind it whenever a step failed, and a PvE summary that
    # under-reports the tick would make a finished run look like it stopped early.
    final_tick=int(native.capture["tick"])
    summary={"generation":generation,"mode":"pve","variant":pve.get("mode"),
             "label":pve.get("label"),"tick":final_tick,"decisions":decisions,
             "waves_armed":len(armed),"waves_total":len(waves),
             "waves_rejected":rejected,"native_plays":plays,
             # The defence verdict, not the engine's duel verdict. `winner` uses
             # the UI's convention: 0 is the human seat.
             "winner":pve_winner(verdict),"lost":verdict.lost,
             "lostTick":lost_tick,"kingTower":verdict.to_json(),
             "kingHpRatio":hp_ratio,
             "stars":stars if pve_winner(verdict)==0 else 0,
             "engineWinner":adapter.observation.terminal.winner,
             "seconds":time.perf_counter()-start}
    if rogue:
        # The run's own progression, so a device log says which dungeon was
        # played, how far it got and what it drafted -- the evidence a nine-draft
        # interlude cannot be verified from the result dialog alone.
        summary["roguelike"]={
            "dungeon":rogue.get("mode"),
            "drafts":len(rogue_state.get("history") or []),
            "draftedWaves":sorted(drafted_waves),
            "buffs":rogue_state.get("stacks"),
            "elixirRate":effective_elixir_rate(rogue_state,final_tick),
            "rageSpeed":rogue_state.get("rage_speed",0.0),
            "extraCards":rogue_state.get("extra_cards"),
            "aiAlly":bool(rogue.get("aiAlly")),
            "waveReached":1+len(drafted_waves),
        }
    log.i("FirstlightLocal","PvE verdict winner=%d lost=%s tick=%s"%(
        summary["winner"],summary["lost"],summary["lostTick"]))
    # Install the verdict so the result dialog reports the defence outcome
    # rather than the engine's duel outcome. Title and message are chosen here
    # because a defence has no "opponent tower destroyed" to celebrate.
    if pve_winner(verdict)==0:
        star_str="⭐"*stars
        variant_name=pve.get("label") or "PvE 挑战"
        title=f"守住了 {star_str}"
        pct=int(hp_ratio*100)
        message=f"【{variant_name}】通关成功！\n获得评价：{star_str} ({stars}星)\n国王塔剩余血量：{pct}%"
        if rogue:
            # A roguelike run is a descent, so the dialog reports how deep it got
            # and how many upgrades it took -- the two numbers the mode is about.
            message+="\n深入第 %d/10 波 · 强化 %d 次"%(
                1+len(drafted_waves),len(rogue_state.get("history") or []))
    else:
        title="王塔被推掉了"
        message="敌人突破了你的防线。"
        if rogue:
            # The loss has to say *where* the run ended, or "you died" is the only
            # feedback a ten-wave descent gives the player.
            message+="\n坚持到了第 %d/%d 波"%(
                max(1,1+len(drafted_waves)),WAVE_COUNT)
    runtime.setScriptedResult(generation,pve_winner(verdict),title,message)
    # The engine only raises the result dialog when its own `finalized` flag
    # goes true, and neither exit above is that condition: the defence ended on
    # the player's king falling or on the schedule running out. Without this
    # the verdict is installed and never consumed, and the player is left on a
    # frozen battle screen with no dialog and no way home.
    if not adapter.observation.terminal.ended:
        try:
            runtime.showScriptedResult(generation)
        except Exception as _e:
            log.w("FirstlightLocal","PvE showResult error: %s"%_e)
    if tracker is not None:
        _emit_probe_report(log,tracker.report())
    return json.dumps(summary)

# logcat drops a single log entry whose payload exceeds roughly 4 KB, and it
# drops it *silently* -- no truncation marker, no error. A PvE match records tens
# of thousands of (tick,x,y) samples, so folding them into the summary JSON that
# Java hands to Log.i loses the whole series: the match ends, the app returns
# home, and the evidence is simply never written anywhere. So the series is
# emitted as its own stream of bounded lines instead, one per tracked unit per
# sample chunk, each comfortably under the limit.
PROBE_LOG_PREFIX="PvE track "
PROBE_LOG_BUDGET=3000

def _emit_probe_report(log,report):
    """Write the position series to logcat in chunks logcat will actually keep.

    Each track is emitted independently so a single huge unit cannot push the
    rest of the evidence out, and samples are sliced into fixed-size groups so
    the worst case -- one unit that lived the whole match at full cadence --
    still splits into several lines instead of one dropped one.
    """
    log.i("FirstlightLocal","PvE probe begin tracks=%d every=%d"%(
        len(report.get("tracks") or []),int(report.get("sampleEveryTicks",0))))
    for index,track in enumerate(report.get("tracks") or []):
        head="%s%d owner=%d card=%s obj=%s"%(
            PROBE_LOG_PREFIX,index,track["owner"],track["cardId"],track["nativeObjectId"])
        # (tick,x,y) triples serialise to a short fixed token, so the budget can
        # be counted in samples rather than estimated.
        samples=track.get("samples") or []
        per_line=max(1,(PROBE_LOG_BUDGET-len(head)-64)//24)
        for start in range(0,len(samples),per_line):
            chunk=samples[start:start+per_line]
            body=" ".join("%d:%d:%d"%(t,x,y) for t,x,y in chunk)
            log.i("FirstlightLocal","%s [%d/%d] %s"%(
                head,(start//per_line)+1,(len(samples)+per_line-1)//per_line,body))
    log.i("FirstlightLocal","PvE probe end")

def run_device_match(generation,schema_json,match_json):
    from java import jclass
    import time
    runtime=jclass("com.firstlight.offline.LocalBattleRuntime")
    native=DeviceNative(generation,runtime)
    match_data=json.loads(match_json)
    if match_data.get("battle",{}).get("gamemode")==72000014 or match_data.get("battle",{}).get("plt")==2:
        return run_2v2_device_match(generation,runtime,native,match_data)
    if match_data.get("pve"):
        return run_pve_device_match(generation,runtime,native,match_data,schema_json)
    if match_data.get("aiDuel"):
        return run_duel_device_match(generation,runtime,native,match_data,schema_json)
    adapter=MobileFair(native.capture,match=match_data)
    adapter.native=native
    adapter.environment.native=native
    schema=json.loads(schema_json)
    decisions=0
    seen_plays=set()
    seen_forms=set()
    log=jclass("android.util.Log")
    plays=[0,0]
    start=time.perf_counter()
    log.i("FirstlightLocal","Match game mode resolved to %s"%(match_data.get("battle",{}).get("gamemode"),))
    while not adapter.observation.terminal.ended:
        adapter.batch=adapter.tensorizer.tensorize(adapter.observation)
        tick=adapter.observation.tick
        actions={}
        if tick>=90:
            inputs=adapter.ordered_inputs(schema)
            decision=json.loads(str(runtime.infer(generation,tick,inputs)))
            decoded=adapter.decode(decision)
            actions={1:decoded.actions}
            decisions+=1
        observations,*_=adapter.environment.step(actions,record_trace=False,observation_owners=(1,),policy_features_only=True)
        adapter.observation=observations[1]
        # Native consume/spawn records attest the form actually used. These
        # diagnostics never become model inputs or a source of game authority.
        for event in native.capture["rich"]["combatEvents"]["events"]:
            context=event.get("deploymentContext")
            identity=(event.get("stateEpoch"),event["sequence"])
            if context and event["kind"] in ("card_play","spawn") and identity not in seen_forms:
                seen_forms.add(identity)
                if context.get("formCode",0):
                    log.i("FirstlightLocal","Native form "+json.dumps({"generation":generation,"kind":event["kind"],
                        "tick":event["tick"],"owner":context["owner"],"card":context["playedCardGlobalId"],
                        "form":context["formCode"],"object":event["target"].get("nativeObjectId")},separators=(",",":")))
        for owner in (0,1):
            for event in adapter.environment.deployment_timing_events(owner):
                if event["kind"]=="card_play" and event["nativeEventId"] not in seen_plays:
                    seen_plays.add(event["nativeEventId"])
                    plays[owner]+=1
                    runtime.recordNativePlay(generation,owner,event["tick"],event["cardId"])
    return json.dumps({"generation":generation,"tick":adapter.observation.tick,"decisions":decisions,
                       "native_plays":plays,"seconds":time.perf_counter()-start})

def validate_capture_directory(directory):
    paths=sorted(Path(directory).glob("tick-*.json"))
    adapter=MobileFair(json.loads(paths[0].read_text()))
    for path in paths[1:]:
        batch=adapter.observe(json.loads(path.read_text()))
    return {"frames": len(paths),"last_tick":adapter.observation.tick,"entities":len(adapter.observation.entities)}

def verify_device_frames(directory):
    import time
    root=Path(directory)
    expected=json.loads((root/"reference.json").read_text())
    schema=json.loads((root/"schema.json").read_text())
    paths=sorted(root.glob("tick-*.json"))
    start=time.perf_counter()
    adapter=MobileFair(json.loads(paths[0].read_text()))
    initialized=time.perf_counter()-start
    durations=[]
    for path, reference in zip(paths[1:],expected,strict=True):
        start=time.perf_counter()
        adapter.observe(json.loads(path.read_text()))
        hashes=[hashlib.sha256(value).hexdigest() for value in adapter.ordered_inputs(schema)]
        if hashes != reference["sha256"]:
            raise AssertionError(f"Android tensor parity failed at {adapter.observation.tick}")
        adapter.exercise_decoding()
        durations.append((time.perf_counter()-start)*1000)
    durations.sort()
    return json.dumps({"passed":True,"frames":len(durations),"tensors":len(durations)*89,
                       "init_seconds":initialized,"median_ms":durations[len(durations)//2],
                       "p95_ms":durations[int(len(durations)*.95)-1],"maximum_ms":max(durations)})

def smoke():
    from native_runner.battle_env import BattleEnvV1
    from native_runner.training.v4.tensorizer import UniversalObservationTensorizerV4
    from native_runner.card_specs import build_card_catalog
    return json.dumps({"ready": True, "catalog_cards": len(build_card_catalog().specs)})
