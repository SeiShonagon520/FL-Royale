package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

/**
 * Android selector for native game modes (elixir/time variations and deck/rule variations).
 */
public final class GameModePicker {
    private static final String PREFS = "firstlight_offline_game_mode";
    private static final String SELECTED_MODE = "selected_mode";
    /** Marks a mode whose pacing inputs were verified to stay inside the trained policy domain. */
    private static final String VERIFIED = "已验证：模型输入在训练范围内";

    public static final class Mode {
        public final String id;
        public final int gameModeId;
        public final String name;
        public final String label;
        public final String category;
        public final String description;
        /** Non-null when the mode was verified to stay inside the trained policy input domain. */
        public final String policyNote;
        /**
         * True when the mode's opposing side is a script rather than a model.
         *
         * A PvE wave is deployed by the schedule, not chosen by an AI, so asking
         * the player to pick an opponent for it is a question with no answer.
         * The mode still needs a policy to play the player's own side, but that
         * is the model already loaded, not a selection.
         */
        public final boolean scripted;

        public Mode(String id, int gameModeId, String name, String label, String category, String description) {
            this(id, gameModeId, name, label, category, description, null, false);
        }

        public Mode(String id, int gameModeId, String name, String label, String category, String description,
                    String policyNote) {
            this(id, gameModeId, name, label, category, description, policyNote, false);
        }

        public Mode(String id, int gameModeId, String name, String label, String category, String description,
                    String policyNote, boolean scripted) {
            this.id = id;
            this.gameModeId = gameModeId;
            this.name = name;
            this.label = label;
            this.category = category;
            this.description = description;
            this.policyNote = policyNote;
            this.scripted = scripted;
        }
    }

    /** True when the given mode key runs a scripted opposing side. */
    public static boolean isScripted(String modeId) {
        for (Mode mode : MODES) {
            if (mode.id.equals(modeId)) return mode.scripted;
        }
        return false;
    }

    /** The scripted mode currently chosen, or null when a normal mode is active. */
    public static Mode selectedScriptedMode(Context context) {
        Mode mode = selectedMode(context);
        return mode != null && mode.scripted ? mode : null;
    }

    /**
     * Whether a roguelike climb is played with an AI ally beside the player.
     *
     * <p>Default off: the mode is a solo defence, one player against ten waves.
     * Ticking it switches the run to the 2v2 arena (location 15000020) and seats
     * team_bot.py as a second defender, which is the "AI 队友助战" the mode
     * description promises. It is a property of the *run*, not of the mode, so it
     * is remembered separately and only consulted for a scripted climb.
     */
    private static final String ALLY_PREF = "firstlight_offline_pve_ally";

    public static boolean aiAllyEnabled(Context context) {
        if (context == null) return false;
        return context.getSharedPreferences(ALLY_PREF, Context.MODE_PRIVATE)
                .getBoolean("ai_ally", false);
    }

    public static boolean rememberAiAlly(Context context, boolean enabled) {
        Objects.requireNonNull(context, "context");
        return context.getSharedPreferences(ALLY_PREF, Context.MODE_PRIVATE)
                .edit().putBoolean("ai_ally", enabled).commit();
    }

    /**
     * Ask "AI 队友助战?" for a scripted climb.
     *
     * <p>Only offered for a roguelike climb: on any other mode the question has
     * no meaning, and a checkbox that silently does nothing is worse than no
     * checkbox. Returns the chosen value to the caller.
     */
    public interface AllyChoice {
        void onChosen(boolean enabled);
    }

    public static void askAiAlly(Activity activity, AllyChoice choice) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) {
            if (choice != null) choice.onChosen(false);
            return;
        }
        final boolean[] resolved = {false};
        final boolean[] enabled = {aiAllyEnabled(activity)};
        new AlertDialog.Builder(activity, android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("AI 队友助战")
                .setMessage("勾选后由 AI 队友协同防守（2v2 对称竞技场），"
                        + "不勾选则单人独挑大梁（标准 3+3 竞技场）。")
                .setMultiChoiceItems(new String[]{"勾选「AI 队友助战」"},
                        new boolean[]{enabled[0]}, (dialog, which, checked) -> {
                            enabled[0] = checked;
                            rememberAiAlly(activity, checked);
                        })
                .setPositiveButton("开始副本", (dialog, which) -> {
                    resolved[0] = true;
                    if (choice != null) choice.onChosen(enabled[0]);
                })
                .setNegativeButton("取消", (dialog, which) -> {
                    if (!resolved[0] && choice != null) choice.onChosen(false);
                })
                .setOnCancelListener(dialog -> {
                    if (!resolved[0] && choice != null) choice.onChosen(false);
                })
                .show();
    }

    public static final List<Mode> MODES;
    static {
        List<Mode> list = new ArrayList<>();
        // 圣水与时间变种 (Elixir & Time Variations)
        list.add(new Mode("ladder", 72000006, "Ladder", "常规天梯 (1v1)", "圣水与时间变种",
                "经典排位赛制（前2分钟1x，后1分钟2x，加时3x圣水）"));
        list.add(new Mode("double_elixir", 72000001, "DoubleElixir", "双倍圣水", "圣水与时间变种",
                "开局即享双倍圣水，快节奏防守反击与强攻"));
        list.add(new Mode("triple_elixir", 72000476, "TripleElixir", "三倍圣水", "圣水与时间变种",
                "全场三倍圣水高速充能，大费压制推线"));
        list.add(new Mode("7x_elixir", 72000493, "7xElixir", "7倍圣水无限火力", "圣水与时间变种",
                "圣水秒速回满，高费卡牌连环倾泻狂欢", VERIFIED));
        list.add(new Mode("sudden_death", 72000483, "SuddenDeath", "突然死亡模式", "圣水与时间变种",
                "双倍圣水加时赛，任一方先破一塔立即终局决胜！", VERIFIED));
        list.add(new Mode("ramp_up", 72000500, "RampUp", "疯狂加速模式", "圣水与时间变种",
                "圣水随时间阶梯递增（1x → 2x → 3x），后期爆发激战",
                "AI 圣水节奏按 3x 封顶（游戏本身最高 5x），避免超出模型训练范围"));

        // 卡组与规则变种 (Deck & Rule Variations)
        list.add(new Mode("mirror", 72000489, "MirrorMatch", "镜像对决模式", "卡组与规则变种",
                "双方使用完全相同的卡组与塔楼，纯技术镜像博弈"));
        list.add(new Mode("all_random", 72000491, "AllRandom", "全随机卡组模式", "卡组与规则变种",
                "系统随机抽取8张卡牌与塔楼对战，考验临场应变", VERIFIED));
        list.add(new Mode("all_evolution", 72000491, "AllRandom", "全觉醒模式", "卡组与规则变种",
                "双方各 8 张卡全部以觉醒形态上场，8 张全部可以变身",
                "模型未针对多觉醒对局训练，AI 决策可能偏保守"));

        // 活动与竞技场变种 (Event & Arena Variations)
        // All of these run the standard 1v1 map with a normal player deck; only
        // the pacing differs, and every one stays inside the trained policy
        // domain once the elixir multiplier is capped at 3x.
        list.add(new Mode("rage", 72000073, "Rage", "怒火模式", "活动与竞技场变种",
                "圣水阶梯更陡（1x → 2.1x → 3x），中后期爆发力惊人", VERIFIED));
        list.add(new Mode("crazy_arena", 72000502, "CrazyArena", "混沌竞技场", "活动与竞技场变种",
                "全程高速圣水充能，节奏比天梯快一档的消耗战", VERIFIED));
        list.add(new Mode("elixir_barrel", 72000384, "ElixirBarrel", "圣水桶", "活动与竞技场变种",
                "开局仅 3 圣水，圣水回充极慢，重策略轻莽撞", VERIFIED));
        list.add(new Mode("chess_board", 72000373, "ChessBoard", "棋盘主题活动", "活动与竞技场变种",
                "双倍转三倍圣水（2x → 3x），开局 3 圣水的慢热对局", VERIFIED));
        list.add(new Mode("crazy_sudden_death", 72000511, "CrazySuddenDeath", "混沌·决胜", "活动与竞技场变种",
                "全程决胜制：任一方先破一塔立即终局，双倍圣水高风险对攻",
                "AI 按加时赛节奏理解决胜阶段，胜负判断可能偏保守"));

        // AI 自战不在这里：它是「对战方式」而不是玩法，入口在对手选择列表里。

        // 肉鸽爬塔 (Roguelike Tower Defence)
        // A PvE climb is a fixed ten-wave descent: the enemy side is not a policy
        // seat at all, its eight cards are the wave table, deployed at the exact
        // ticks the schedule names via LocalBattleRuntime.scheduleCard, and
        // between every pair of waves the player takes a three-pick-one upgrade.
        // The three dungeons differ by which units their waves name and which
        // pair of bosses closes the run, not by any rule change.
        list.add(new Mode("pve_goblin", 72000018, "PvE_Goblin", "哥布林秘境·极速狂潮", "肉鸽爬塔",
                "10 波极速狂潮，投矛手与蛮族蜂拥群攻，第 10 波狂暴双巨魔", null, true));
        list.add(new Mode("pve_undead", 72000018, "PvE_Undead", "幽冥墓园·不死空袭", "肉鸽爬塔",
                "10 波不死空袭，骷髅军团与气球兵空袭，第 10 波巨型骷髅 + 地狱飞龙", null, true));
        list.add(new Mode("pve_heavy", 72000018, "PvE_Heavy", "重装熔炉·钢铁巨擘", "肉鸽爬塔",
                "10 波钢铁推进，哥仑石人重甲压线，第 10 波超级皮卡 + 超级超骑", null, true));

        // 多人协同对战 (Team Battle Variations)
        // A team match seats four players: {0,1} versus {2,3}, on the stock 2v2
        // arena (location 15000020). Owner 0 is the human and owner 1 is an AI
        // teammate driven by team_bot.py; neither is a policy seat, so the
        // player still plays their own cards from the screen.
        list.add(new Mode("2v2", 72000014, "TeamVsTeam", "2v2 协同混战", "多人协同对战",
                "1名真人+1名AI队友协同防守，对抗2名AI敌人，双国王塔制"));
        MODES = Collections.unmodifiableList(list);
    }

    private GameModePicker() {}

    public static Mode selectedMode(Context context) {
        if (context == null) return MODES.get(0);
        String saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(SELECTED_MODE, "ladder");
        for (Mode mode : MODES) {
            if (mode.id.equals(saved)) {
                return mode;
            }
        }
        return MODES.get(0);
    }

    public static boolean rememberSelectedMode(Context context, String id) {
        Objects.requireNonNull(context, "context");
        for (Mode mode : MODES) {
            if (mode.id.equals(id)) {
                return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                        .edit().putString(SELECTED_MODE, id).commit();
            }
        }
        return false;
    }

    public static void show(Activity activity, Runnable onSelected) {
        show(activity, onSelected, null);
    }

    public static void show(Activity activity, Runnable onSelected, Runnable onCancelled) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) {
            if (onCancelled != null) onCancelled.run();
            return;
        }
        Mode current = selectedMode(activity);
        String[] labels = new String[MODES.size()];
        int selectedIndex = 0;
        for (int i = 0; i < MODES.size(); i++) {
            Mode mode = MODES.get(i);
            labels[i] = mode.label + (mode.id.equals(current.id) ? "  ✓" : "");
            if (mode.id.equals(current.id)) selectedIndex = i;
        }

        ArrayAdapter<String> adapter = new ArrayAdapter<String>(activity,
                android.R.layout.simple_list_item_2, android.R.id.text1, labels) {
            @Override public View getView(int position, View convertView, ViewGroup parent) {
                View view = super.getView(position, convertView, parent);
                Mode mode = MODES.get(position);
                TextView text = view.findViewById(android.R.id.text1);
                if (text != null) {
                    text.setText(mode.label + (mode.id.equals(current.id) ? "  [当前选择]" : "")
                            + (mode.policyNote != null ? "  ✓" : ""));
                    text.setTextColor(mode.id.equals(current.id) ? 0xff0066cc : 0xff161616);
                    text.setTextSize(16);
                }
                TextView desc = view.findViewById(android.R.id.text2);
                if (desc != null) {
                    StringBuilder line = new StringBuilder("【").append(mode.category).append("】")
                            .append(mode.description);
                    if (mode.policyNote != null) line.append("\n✓ ").append(mode.policyNote);
                    desc.setText(line.toString());
                    desc.setTextColor(0xff666666);
                    desc.setTextSize(13);
                }
                return view;
            }
        };

        // The catalogue is long enough -- nineteen entries across six categories --
        // that a dialog sized to its content either overflows the screen or
        // squeezes every row into an unreadable stub. A ListView inside a
        // scroll container gives every mode the same room and lets the player
        // reach the PvE entries by scrolling instead of hunting.
        ListView list = new ListView(activity);
        list.setAdapter(adapter);
        list.setDivider(null);
        list.setDividerHeight(0);
        list.setVerticalScrollBarEnabled(true);
        // Select the current mode so a re-open lands where the player left off
        // rather than at the top of nineteen rows.
        list.setSelection(selectedIndex);
        int rowPx = Math.round(62 * activity.getResources().getDisplayMetrics().density);
        list.setMinimumHeight(Math.min(rowPx * 5, activity.getResources()
                .getDisplayMetrics().heightPixels / 2));
        list.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        final boolean[] selected = {false};
        final AlertDialog[] dialogRef = new AlertDialog[1];
        // setView() replaces setAdapter()'s built-in click handling, so the
        // selection has to be wired by hand or the list becomes inert.
        list.setOnItemClickListener((parent, view, position, id) -> {
            selected[0] = true;
            Mode chosen = MODES.get(position);
            rememberSelectedMode(activity, chosen.id);
            Toast.makeText(activity, "已选择玩法：" + chosen.label, Toast.LENGTH_SHORT).show();
            if (dialogRef[0] != null) {
                dialogRef[0].dismiss();
            }
            if (onSelected != null) {
                onSelected.run();
            }
        });

        AlertDialog dialog = new AlertDialog.Builder(activity, android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("选择对战玩法模式")
                .setView(list)
                .setNegativeButton("取消", (d, which) -> {
                    if (!selected[0] && onCancelled != null) {
                        onCancelled.run();
                    }
                })
                .create();
        dialogRef[0] = dialog;
        dialog.setOnDismissListener(d -> {
            if (!selected[0] && onCancelled != null) {
                onCancelled.run();
            }
        });
        dialog.show();
    }
}
