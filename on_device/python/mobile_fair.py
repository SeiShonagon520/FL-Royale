"""Android-local bridge for the pinned Firstlight FAIR implementation."""
import json
import hashlib
from pathlib import Path

class CapturedNative:
    """Receives same-boundary raw snapshots; never creates or steps a native scene."""
    public_card_play_events_from_combat_ring = True
    def __init__(self, capture): self.capture = capture
    def create_native_match(self, match): return self.capture["ordinary"]
    def observe_atomic(self): return self.capture
    def observe(self): return self.capture["ordinary"]

class MobileFair:
    def __init__(self, capture, actor=1, match=None):
        from native_runner.battle_env import BattleEnvV1
        from native_runner.match_factory import MatchConfig
        from native_runner.training.v4.factory import build_episode_tensorizer_v4,production_semantic_bundle
        bundle=production_semantic_bundle()
        self.native = CapturedNative(capture)
        self.actor = actor
        decks = [tuple(row["cardId"] for row in player["deck"]) for player in capture["ordinary"]["players"]]
        self.environment = BattleEnvV1(native=self.native,
            card_catalog=bundle.native_card_catalog,semantic_subset_contract=bundle.semantic_subset_contract,
            ruleset_id=hashlib.sha256(b"firstlight-local-v1:110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783:e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339").hexdigest(),
            include_native_digest=False)
        availability=[tuple(int(row.get("el",0)) for row in match["battle"][f"deck{owner}"]["sp"])
                      if match else (0,)*8 for owner in (0,1)]
        if match and any(tuple(row["d"] for row in match["battle"][f"deck{owner}"]["sp"])!=decks[owner] for owner in (0,1)):
            raise ValueError("Native deck differs from the configured local match")
        observations, info = self.environment.reset(match_config=MatchConfig(deck0=decks[0],deck1=decks[1],king_tower_level=11,
            deck0_form_availability=availability[0],deck1_form_availability=availability[1],
            tower_troop0_id=match["battle"]["deck0"]["sc"][0]["d"] if match else 159000000,
            tower_troop1_id=match["battle"]["deck1"]["sc"][0]["d"] if match else 159000000),
            options={"render_mode":"native-render","decision_ticks":5,"event_driven_decisions":False})
        self.tensorizer = build_episode_tensorizer_v4(self.environment.episode_config, actor_owner=actor)
        self.tensorizer.start_episode(observations[actor],initial_elixir={0:6.0,1:6.0})
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

def prewarm():
    from native_runner.training.v4.factory import production_semantic_bundle
    from native_runner.rich_telemetry_adapter import load_default_effect_catalog
    from native_runner.timeline import load_game_mode_timeline
    production_semantic_bundle()
    load_default_effect_catalog()
    load_game_mode_timeline(72000006)
    return "ready"

def create_match(deck_json,ai_id,opponent_json=""):
    from native_runner.match_factory import MatchConfig
    from native_runner.competitive_data import load_competitive_data
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
        # Home stores every unlocked form. A match equips only its native
        # Evo, Hero and flexible slots; FAIR's deck roles describe equipment.
        active=value.get("active")
        if active is None:
            active=[(1 if mask&1 else 0) if slot==0 else (2 if mask&2 else 0) if slot==1 else
                    (1 if mask&1 else 2 if mask&2 else 0) if slot==2 else 0
                    for slot,mask in enumerate(masks)]
        if len(active)!=8 or any(form not in (0,1,2) or form and not masks[slot]&(1<<(form-1))
                                 for slot,form in enumerate(active)):
            raise ValueError("Invalid equipped form")
        if active[0] not in (0,1) or active[1] not in (0,2) or any(active[3:]):
            raise ValueError("Equipped form is outside a native special slot")
        equipped=tuple((1<<(form-1)) if form else 0 for form in active)
        choices={str(support[card]["name"]):form for card,form in zip(cards,active) if form}
        tower=value.get("tower",159000000)
        if isinstance(tower,bool) or not isinstance(tower,int) or tower not in (159000000,159000001,159000002,159000004):
            raise ValueError("Unsupported local tower troop")
        return cards,equipped,choices,tower
    player=json.loads(deck_json)
    human=decode(player)
    if opponent_json:
        enemy=decode(json.loads(opponent_json))
    elif ai_id.startswith("hog26_"):
        hog=(26000021,26000014,26000038,26000030,26000010,27000000,28000000,28000011)
        enemy=decode({"cards":hog,"forms":[int(support[card]["form_availability"]) for card in hog]})
    else: enemy=human
    payload=MatchConfig(deck0=human[0],deck1=enemy[0],deck0_form_availability=human[1],deck1_form_availability=enemy[1],
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
        observed=int(self.capture["tick"])
        due=execute_tick if execute_tick is not None else observed+int(execute_in_ticks)
        card=self.capture["ordinary"]["players"][action.owner]["hand"][action.hand_index]["cardId"]
        if action.owner!=1: raise ValueError("AI may only control owner 1")
        receipt=json.loads(str(self.runtime.queueAction(self.generation,observed,due,action.hand_index,card,action.x,action.y,False,0,0)))
        if not receipt.get("ok"): raise RuntimeError(receipt)
        return receipt
    def queue_ability_action_at(self,action,*,execute_in_ticks):
        from native_runner.cr_native_env import RunnerError
        observed=int(self.capture["tick"])
        if action.owner!=1: raise ValueError("AI may only control owner 1")
        receipt=json.loads(str(self.runtime.queueAction(self.generation,observed,observed+execute_in_ticks,-1,0,0,0,True,action.object_index,action.secondary_index)))
        if not receipt.get("ok"): raise RunnerError("could not resolve one ready native champion ability source")
        return receipt

def run_device_match(generation,schema_json,match_json):
    from java import jclass
    import time
    runtime=jclass("com.firstlight.offline.LocalBattleRuntime")
    native=DeviceNative(generation,runtime)
    adapter=MobileFair(native.capture,match=json.loads(match_json))
    adapter.native=native
    adapter.environment.native=native
    schema=json.loads(schema_json)
    decisions=0
    seen_plays=set()
    seen_forms=set()
    log=jclass("android.util.Log")
    plays=[0,0]
    start=time.perf_counter()
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
