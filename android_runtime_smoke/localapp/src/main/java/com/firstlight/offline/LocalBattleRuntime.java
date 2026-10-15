package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.text.InputType;
import android.util.Log;
import android.widget.EditText;
import android.widget.Toast;
import com.chaquo.python.Python;
import com.chaquo.python.PyObject;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** Entire match worker lives in the APK. JNI only exchanges bounded messages. */
public final class LocalBattleRuntime {
    private static final String TAG="FirstlightLocal";
    private static final AtomicBoolean started=new AtomicBoolean();
    private static final ExecutorService worker=Executors.newSingleThreadExecutor(task -> {
        Thread thread=new Thread(task,"firstlight-local-battle"); thread.setDaemon(true); return thread;
    });
    private static Activity activity;
    private static Context application;
    private static PyObject module;
    private static String schema;
    private static RecurrentPolicySession recurrent;
    private static GameOrtBootstrap.PolicyLease lease;
    /** Duel only: one independent session per AI seat, because the recurrent
     *  session rejects a second owner's tick and would poison the first. */
    private static RecurrentPolicySession[] duelSessions;
    private static DuelPolicyRegistry.DuelLease[] duelLeases;
    /** Set by the picker just before the native handoff so the next
     *  prepareMatch builds a two-AI fixture instead of a human-vs-AI one. */
    private static volatile boolean duelRequested;
    private static String matchConfiguration;
    private static String homeFixture;
    private static volatile long activeGeneration, abandonedGeneration, endedGeneration;
    private static AlertDialog resultDialog, leaveDialog, renameDialog, homeProblemDialog, draftDialog;
    // A scripted (wave-driven) match computes its own verdict from the player's
    // king tower instead of the engine's opposing-tower count. It is installed
    // by the Python runner and consumed by the next showResult for that same
    // generation.
    private static final Object RESULT_LOCK=new Object();
    private static volatile long scriptedResultGeneration;
    private static volatile int scriptedResultWinner;
    private static volatile String scriptedResultTitle, scriptedResultMessage;

    public static void start(Context context) {
        if(!started.compareAndSet(false,true)) return;
        if(context instanceof Activity) activity=(Activity)context;
        nativeAttach();
        if(activity!=null) activity.getApplication().registerActivityLifecycleCallbacks(new android.app.Application.ActivityLifecycleCallbacks() {
            public void onActivityCreated(Activity value,android.os.Bundle state) {}
            public void onActivityStarted(Activity value) {}
            public void onActivityResumed(Activity value) { if(value==activity) nativeSetForeground(true); }
            public void onActivityPaused(Activity value) { if(value==activity) nativeSetForeground(false); }
            public void onActivityStopped(Activity value) {}
            public void onActivitySaveInstanceState(Activity value,android.os.Bundle state) {}
            public void onActivityDestroyed(Activity value) {
                if(value==activity) {
                    if(resultDialog!=null) { resultDialog.dismiss(); resultDialog=null; }
                    if(leaveDialog!=null) { leaveDialog.dismiss(); leaveDialog=null; }
                    if(renameDialog!=null) { renameDialog.dismiss(); renameDialog=null; }
                    if(homeProblemDialog!=null) { homeProblemDialog.dismiss(); homeProblemDialog=null; }
                    activity=null;
                }
            }
        });
        Context app=context.getApplicationContext();
        application=app;
        homeFixture=defaultHomeFixture(playerName());
        nativeBootReady(homeFixture);
        Log.i(TAG,"Native offline Home boot ready dispatched immediately");
        worker.execute(() -> {
            try {
                long began=System.nanoTime();
                if(!Python.isStarted()) Python.start(new AndroidPlatform(app));
                module=Python.getInstance().getModule("mobile_fair");
                module.callAttr("prewarm");
                try(java.io.InputStream input=app.getAssets().open("firstlight/abi/production_tensor_schema_uint8.json")) {
                    java.io.ByteArrayOutputStream output=new java.io.ByteArrayOutputStream();
                    byte[] buffer=new byte[8192]; int count;
                    while((count=input.read(buffer))!=-1) output.write(buffer,0,count);
                    schema=new String(output.toByteArray(),StandardCharsets.UTF_8);
                }
                Log.i(TAG,"Offline runtime ready ms="+(System.nanoTime()-began)/1e6);
            } catch(Throwable error) { Log.e(TAG,"Offline bootstrap failed",error); showResult(0,-2); }
        });
    }

    private static String defaultHomeFixture(String playerName) {
        int rndSeed = (int)(Math.random() * 1000000000) + 1;
        return "{\"battle\":{\"gmt\":1,\"plt\":1,\"t1s\":0,\"t2s\":0,\"gamemode\":72000006,\"hm\":false,\"lvlcap\":11,\"cardlvlmin\":11,\"stp\":0,\"trail\":170000000,\"te\":-1,\"tps\":1,\"deck0\":{\"hdr\":\"BD01\",\"sp\":[{\"d\":26000000},{\"d\":26000001},{\"d\":26000005},{\"d\":28000001},{\"d\":28000000},{\"d\":26000003},{\"d\":26000014},{\"d\":26000018}],\"sc\":[{\"d\":159000000,\"l\":1,\"t\":0,\"c\":1}]},\"deck1\":{\"hdr\":\"BD01\",\"sp\":[{\"d\":26000000},{\"d\":26000001},{\"d\":26000005},{\"d\":28000001},{\"d\":28000000},{\"d\":26000003},{\"d\":26000014},{\"d\":26000018}],\"sc\":[{\"d\":159000000,\"l\":1,\"t\":0,\"c\":1}]},\"avatar0\":{\"accountID.lo\":1,\"accountID.hi\":0,\"expLevel\":11,\"name\":\"" + playerName + "\",\"arena\":54000001,\"welo\":-1,\"scr\":0,\"lti\":0,\"npc\":false},\"avatar1\":{\"accountID.lo\":2,\"accountID.hi\":0,\"expLevel\":11,\"name\":\"general\",\"arena\":54000001,\"welo\":-1,\"scr\":0,\"lti\":0,\"npc\":false},\"location\":15000199,\"arena\":54000001,\"hbd\":[{\"em\":{\"ce\":[],\"de\":[]},\"sk\":{\"os\":[],\"ss\":[],\"rnd\":false},\"bn\":{\"bg\":0,\"fg\":0},\"kt\":11},{\"em\":{\"ce\":[],\"de\":[]},\"sk\":{\"os\":[],\"ss\":[],\"rnd\":false},\"bn\":{\"bg\":0,\"fg\":0},\"kt\":11}]},\"endTick\":7200,\"cmd\":[],\"evt\":[],\"rndSeed\":" + rndSeed + ",\"time\":-1}";
    }

    /** Called by the picker when the next native handoff is an AI duel. */
    public static void beginDuel() { duelRequested=true; }

    public static void prepareMatch(long request,String aiId,String deck) {
        worker.execute(() -> {
            try {
                Context context=activity!=null?activity:application;
                // The duel handoff passes a real seat-0 model id so the native
                // ValidAiId whitelist accepts it, and records the intent through
                // beginDuel(). A duel is never a game mode, so a stale mode
                // selection can no longer strand the player here.
                if(duelRequested) { duelRequested=false; prepareDuelMatch(request,deck); return; }
                GameModePicker.Mode mode=GameModePicker.selectedMode(context);
                GameOrtBootstrap.PolicyLease selected=GameOrtBootstrap.acquire();
                if(selected.policy==null || !aiId.equals(selected.aiId) || !GameOrtBootstrap.isCurrent(selected))
                    throw new IllegalStateException("Selected policy changed");
                String opponent=AiDeckSettings.savedDeck(context,aiId);
                String teammate=AiDeckSettings.savedDeck(context,"teammate");
                // The "AI 队友助战" toggle is a property of a roguelike run, not of
                // a mode, so it rides on the deck payload the picker already owns.
                // create_match reads player["aiAlly"] to choose the arena (standard
                // 3+3 solo, or 2v2 symmetric with the ally). Only a scripted climb
                // consults it; every other mode ignores the field entirely.
                String configuredDeck=withAiAlly(deck,context);
                String fixture=withPlayerName(module.callAttr("create_match",configuredDeck,aiId,opponent,mode.id,teammate).toString(),playerName());
                String opponentName=null;
                for(AiOpponentPicker.Choice choice:AiOpponentPicker.readCatalog(application.getAssets()).choices)
                    if(choice.id.equals(aiId)) { opponentName=choice.label; break; }
                if(opponentName==null) throw new IllegalArgumentException("Unknown AI id: "+aiId);
                JSONObject configuration=new JSONObject(fixture);
                JSONObject battle=configuration.getJSONObject("battle");
                battle.getJSONObject("avatar1").put("name",opponentName);
                fixture=configuration.toString();
                matchConfiguration=fixture;
                nativeSubmitMatch(request,fixture);
                JSONObject home=battle.getJSONArray("hbd").getJSONObject(0);
                Log.i(TAG,"Local fixture ready ai="+aiId+" equipment="+new JSONObject()
                    .put("ai_name",opponentName)
                    .put("human_tower",battle.getJSONObject("deck0").getJSONArray("sc").getJSONObject(0).getInt("d"))
                    .put("ai_tower",battle.getJSONObject("deck1").getJSONArray("sc").getJSONObject(0).getInt("d"))
                    .put("emote_slots",home.getJSONObject("em").optJSONArray("de"))
                    .put("skins",home.getJSONObject("sk").optJSONArray("os")==null?0:home.getJSONObject("sk").getJSONArray("os").length())
                    .put("skin",home.getJSONObject("sk").optInt("ats"))
                    .put("banner",home.getJSONObject("bn")));
            } catch(Throwable error) { Log.e(TAG,"Match preparation failed",error); showResult(0,-2); }
        });
    }

    /**
     * AI duel: owner 0 keeps the player's own deck and is driven by the seat-0
     * model, owner 1 uses the AI deck and the seat-1 model. Both policies are
     * opened through the duel registry because GameOrtBootstrap can only hold
     * one model at a time.
     */
    private static void prepareDuelMatch(long request,String deck) throws Exception {
        Context context=activity!=null?activity:application;
        String[] aiIds=AiDuelSetup.selectedAiIds(context);
        String opponent=AiDeckSettings.savedDeck(context,aiIds[1]);
        // A duel is ordinary ladder rules with two AI seats, so the game mode
        // stays the verified ladder: no new pacing gate and no new timeline.
        String fixture=withPlayerName(
            module.callAttr("create_match",deck,aiIds[1],opponent,"ladder","").toString(),playerName());
        JSONObject configuration=new JSONObject(fixture);
        JSONObject battle=configuration.getJSONObject("battle");
        battle.getJSONObject("avatar0").put("name",AiDuelSetup.labelFor(context,aiIds[0]));
        battle.getJSONObject("avatar1").put("name",AiDuelSetup.labelFor(context,aiIds[1]));
        // run_device_match dispatches on this flag: same game mode, both seats AI.
        configuration.put("aiDuel",true);
        configuration.put("aiOwner0",aiIds[0]);
        configuration.put("aiOwner1",aiIds[1]);
        fixture=configuration.toString();
        DuelPolicyRegistry.DuelLease[] leases=new DuelPolicyRegistry.DuelLease[2];
        for(int owner=0;owner<2;owner++)
            leases[owner]=DuelPolicyRegistry.prepare(context,aiIds[owner],
                PolicyModelCatalog.forId(aiIds[owner])).get();
        duelLeases=leases;
        matchConfiguration=fixture;
        nativeSubmitMatch(request,fixture);
        Log.i(TAG,"Duel fixture ready ai0="+aiIds[0]+" ai1="+aiIds[1]);
    }

    public static void onNativeBattleCreated(long generation) {
        activeGeneration=generation;
        // A new battle is entitled to its own result dialog.
        resultShown=false;
        worker.execute(() -> {
            try {
                if(duelLeases!=null) { runDuel(generation); return; }
                lease=GameOrtBootstrap.acquire();
                if(lease.policy==null) throw new IllegalStateException("Policy unavailable");
                recurrent=new RecurrentPolicySession(lease.policy);
                recurrent.beginBattle(generation).get();
                String result=module.callAttr("run_device_match",generation,schema,matchConfiguration).toString();
                Log.i(TAG,"Battle completed "+result);
            } catch(Throwable error) {
                if(abandonedGeneration==generation) Log.i(TAG,"Battle left generation="+generation);
                else if(endedGeneration==generation) Log.i(TAG,"Finalized battle worker closed generation="+generation);
                else {
                    Log.e(TAG,"Battle pipeline failed generation="+generation,error);
                    nativeAbort(generation); showResult(generation,-2);
                }
            } finally {
                if(recurrent!=null) recurrent.abortBattle(generation); recurrent=null; lease=null;
                closeDuelSessions(generation);
            }
        });
    }

    /**
     * Offline Home bootstrap progress, reported by the native scene builder.
     *
     * <p>A device that cannot build the Home scene used to sit on the stock 96%
     * loading screen with no explanation. The native side now publishes a reason
     * and a retry count, and after enough failures it retries with a core-only
     * card collection so the player still reaches the lobby.</p>
     *
     * @param status  0 ok, 1 not isolated, 2 messaging unreadable,
     *                3 profile unreadable, 4 a card failed, 5 degraded
     * @param attempts how many times the scene has been tried
     */
    public static void onHomeBootStatus(int status,int attempts) {
        Log.i(TAG,"Home boot status="+status+" attempts="+attempts);
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        // Success and the transient card retry never need the player's
        // attention; the permanent failures do.
        if(status==0 || status==4) return;
        if(attempts<3) return;
        current.runOnUiThread(() -> showHomeBootProblem(current,status,attempts));
    }

    private static void showHomeBootProblem(Activity current,int status,int attempts) {
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        if(homeProblemDialog!=null && homeProblemDialog.isShowing()) return;
        String title="离线大厅启动失败";
        String message;
        switch(status) {
            case 1:
                message="游戏引擎初始化耗时过长或未就绪，请稍候重试。\n\n已重试"+attempts+" 次。";
                break;
            case 2:
            case 3:
                message="无法读取游戏内部数据（状态"+status+"）。\n\n"
                    +"若长时间卡在96%加载，通常表示设备资源数据未就绪或版本不匹配。\n已重试"+attempts+" 次。";
                break;
            default:
                message="未知原因（状态"+status+"）。\n\n已重试"+attempts+" 次。";
        }
        homeProblemDialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert)
            .setTitle(title)
            .setMessage(message+"\n\n详情请在 adb logcat 中查看 "
                +"FirstlightLocal 和 CRNativeProbe 标签。")
            .setPositiveButton("重试",(d,which)->{
                homeProblemDialog=null;
                if(homeFixture!=null) {
                    nativeBootReady(homeFixture);
                }
            })
            .setNegativeButton("退出",(d,which)->{ homeProblemDialog=null; current.finish(); })
            .setOnCancelListener(d->homeProblemDialog=null)
            .setOnDismissListener(d->homeProblemDialog=null)
            .create();
        homeProblemDialog.show();
    }

    /**
     * One recurrent session per seat. RecurrentPolicySession is single-owner:
     * it enforces an exact tick cadence and poisons itself when a second owner
     * submits, so the two AI seats must never share one.
     */
    private static void runDuel(long generation) throws Exception {
        RecurrentPolicySession[] sessions=new RecurrentPolicySession[2];
        for(int owner=0;owner<2;owner++) {
            if(duelLeases[owner]==null) throw new IllegalStateException("Duel policy unavailable for owner "+owner);
            sessions[owner]=new RecurrentPolicySession(duelLeases[owner].policy);
            sessions[owner].beginBattle(generation).get();
        }
        // Ownership transfers here; closeDuelSessions in the caller's finally
        // block aborts and releases them exactly once.
        duelSessions=sessions;
        String result=module.callAttr("run_device_match",generation,schema,matchConfiguration).toString();
        Log.i(TAG,"Duel completed "+result);
    }

    private static void closeDuelSessions(long generation) {
        if(duelSessions!=null) {
            for(RecurrentPolicySession session:duelSessions)
                if(session!=null) try { session.abortBattle(generation); }
                catch(Throwable ignored) { }
            duelSessions=null;
        }
        if(duelLeases!=null) { DuelPolicyRegistry.closeAll(); duelLeases=null; }
    }

    /** Route one inference to the requested seat's own recurrent session. */
    public static synchronized String inferForOwner(long generation,int owner,long tick,byte[][] inputs) throws Exception {
        RecurrentPolicySession[] sessions=duelSessions;
        if(sessions==null || owner<0 || owner>=sessions.length || sessions[owner]==null)
            throw new IllegalStateException("Duel seat unavailable: "+owner);
        RecurrentPolicySession.Decision decision=sessions[owner].submitStep(generation,tick,inputs).get();
        if(!sessions[owner].isCurrent(decision)) throw new IllegalStateException("Stale duel decision");
        OnDeviceOrtPolicy.StepResult result=decision.policy;
        JSONObject json=new JSONObject();
        json.put("gate",result.gate); json.put("micro_action_count",result.microActionCount);
        json.put("candidate_index",new JSONArray(result.candidateIndex));
        json.put("candidate_uid",new JSONArray(result.candidateUid));
        json.put("target_cell",new JSONArray(result.targetCell));
        json.put("delay_offset_bin",new JSONArray(result.delayOffsetBin));
        if(tick%100==0 || result.microActionCount>0)
            Log.i(TAG,"Duel decision owner="+owner+" tick="+tick+" actions="+result.microActionCount+
                " inference_ms="+result.forwardNanos/1e6);
        return json.toString();
    }

    public static String infer(long generation,long tick,byte[][] inputs) throws Exception {
        if(recurrent==null || !GameOrtBootstrap.isCurrent(lease)) throw new IllegalStateException("Policy changed");
        RecurrentPolicySession.Decision decision=recurrent.submitStep(generation,tick,inputs).get();
        if(!recurrent.isCurrent(decision)) throw new IllegalStateException("Stale decision");
        OnDeviceOrtPolicy.StepResult result=decision.policy;
        JSONObject json=new JSONObject();
        json.put("gate",result.gate); json.put("micro_action_count",result.microActionCount);
        json.put("candidate_index",new JSONArray(result.candidateIndex));
        json.put("candidate_uid",new JSONArray(result.candidateUid));
        json.put("target_cell",new JSONArray(result.targetCell));
        json.put("delay_offset_bin",new JSONArray(result.delayOffsetBin));
        if(tick%100==0 || result.microActionCount>0)
            Log.i(TAG,"AI decision tick="+tick+" actions="+result.microActionCount+" inference_ms="+result.forwardNanos/1e6);
        return json.toString();
    }

    /**
     * Override the result the engine reports for a scripted mode.
     *
     * The engine ends every match the same way -- it counts the *opposing*
     * towers and names a winner once yours are gone. That is the duel rule and
     * the exact inverse of a wave-driven defence, where the player has no enemy
     * tower to destroy and only losing their own king ends the run. The Python
     * runner computes the correct verdict from the player's own king tower and
     * installs it here, so the dialog says what actually happened instead of
     * reporting a win the player did not earn.
     *
     * The override is keyed to a generation and consumed once, so a later match
     * is never labelled by a previous one's verdict.
     */
    public static void setScriptedResult(long generation,int winner,String title,String message) {
        if(generation<=0) return;
        synchronized(RESULT_LOCK) {
            scriptedResultGeneration=generation;
            scriptedResultWinner=winner;
            scriptedResultTitle=title;
            scriptedResultMessage=message;
        }
    }

    /**
     * Show the scripted result dialog for a match the engine did not end.
     *
     * The result dialog is normally raised by the native bridge, and only when
     * the engine's own `finalized` flag goes true. A wave defence breaks out of
     * its Python loop on its own terms -- the player's king tower fell, or the
     * stage schedule ran out -- and neither of those is the engine's
     * opposing-tower condition, so `finalized` stays false and the dialog is
     * never raised. The match is over but the player is left staring at a
     * frozen battle with no way back to the lobby.
     *
     * Calling this from the runner closes that gap: the verdict computed in
     * Python is installed by setScriptedResult, and this raises the dialog that
     * would consume it. The generation guard means a verdict from a finished
     * match can never surface over a later one.
     */
    public static void showScriptedResult(long generation) {
        if(generation<=0 || generation!=activeGeneration || generation==abandonedGeneration) return;
        // The bridge can finalize first and raise the engine's duel verdict
        // before Python installs the defence verdict. That race is real: on a
        // wave defence the player destroys their own towers, which is exactly
        // the engine's "you lost" condition, so it can finalize on the same
        // frame the king falls. The scripted verdict is authoritative, so this
        // replaces whatever the bridge already put up instead of being dropped
        // by the once-per-battle guard.
        resultShown=true;
        showResultForced(generation,Integer.MIN_VALUE,true);
    }

    /**
     * True once a result dialog has been raised for the live battle.
     *
     * A wave defence ends on its own terms, and the engine can finalize a few
     * frames later -- long after the scripted dialog was dismissed and the
     * generation reset to 0. The native bridge then calls showResult again, and
     * because activeGeneration is 0 the guard at the top of showResult lets it
     * through, so a second "对局结束" dialog reappears over the lobby and the
     * player cannot get back in. One dialog per battle: once raised, the battle
     * has reported and the bridge's later call is a duplicate.
     */
    private static volatile boolean resultShown;

    /**
     * Top-of-screen wave alert or HUD notification during PvE matches.
     */
    public static void notifyPveWave(long generation, int waveIndex, int totalWaves, String label, boolean isBoss) {
        if(generation<=0 || generation!=activeGeneration || generation==abandonedGeneration) return;
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        current.runOnUiThread(() -> {
            if(current.isFinishing() || current.isDestroyed()) return;
            if(generation!=activeGeneration || generation==abandonedGeneration) return;
            String text = isBoss
                ? ("⚠️ [BOSS 战] " + label + " (" + (waveIndex + 1) + "/" + totalWaves + ")")
                : ("⚔️ " + label + " (" + (waveIndex + 1) + "/" + totalWaves + ")");
            android.widget.Toast toast = android.widget.Toast.makeText(current, text, android.widget.Toast.LENGTH_SHORT);
            toast.setGravity(android.view.Gravity.TOP | android.view.Gravity.CENTER_HORIZONTAL, 0, 120);
            toast.show();
        });
    }

    /**
     * The roguelike three-pick-one draft, raised between waves.
     *
     * <p>Called from the Python runner once a wave is cleared and its safe
     * interlude has elapsed. {@code optionsJson} is a JSON array of objects
     * carrying {@code id}, {@code title}, {@code description} and {@code icon};
     * the choice is reported back through the Python module's
     * {@code report_draft_choice}, so the runner owns the whole state machine and
     * this class only owns the surface.
     *
     * <p>The specification asks for fifteen seconds "to be sure the player has
     * plenty of time to read every Buff description word by word", so the
     * countdown is shown on screen rather than being an invisible timer: a
     * player who cannot see how long is left cannot decide whether to read the
     * third option carefully. The countdown handler still auto-picks after
     * {@code timeoutSeconds} so a run can never stall on a draft the player
     * walked away from, and the interlude continues either way.
     */
    public static void showRoguelikeDraft(long generation, int waveIndex, String optionsJson) {
        showRoguelikeDraft(generation, waveIndex, optionsJson, 3);
    }

    public static void showRoguelikeDraft(long generation, int waveIndex, String optionsJson,
                                          int timeoutSeconds) {
        if(generation<=0 || generation!=activeGeneration || generation==abandonedGeneration) return;
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        final JSONArray options;
        try {
            options=new JSONArray(optionsJson==null?"[]":optionsJson);
        } catch (org.json.JSONException malformed) {
            Log.w(TAG,"Roguelike draft options are not a JSON array: "+malformed.getMessage());
            return;
        }
        if(options.length()==0) return;
        draftOptions=options;
        // One draft at a time. A second interlude opening while the first dialog
        // is up would stack two modals and orphan the first pick.
        if(draftDialog!=null && draftDialog.isShowing()) return;
        current.runOnUiThread(()->{
            if(current.isFinishing() || current.isDestroyed()) return;
            if(generation!=activeGeneration || generation==abandonedGeneration) return;
            if(draftDialog!=null && draftDialog.isShowing()) return;
            // The result dialog wins: if the run has already ended there is no
            // interlude left to draft into, and leaving this up would strand the
            // player behind a modal they can no longer act on.
            if(resultDialog!=null && resultDialog.isShowing()) return;
            final android.os.Handler handler=new android.os.Handler(current.getMainLooper());
            final boolean[] decided={false};
            final Runnable[] autoPick=new Runnable[1];
            android.widget.LinearLayout list=new android.widget.LinearLayout(current);
            list.setOrientation(android.widget.LinearLayout.VERTICAL);
            int pad=Math.round(12*current.getResources().getDisplayMetrics().density);
            list.setPadding(pad,pad,pad,pad);
            for(int index=0;index<options.length();index++){
                JSONObject option=options.optJSONObject(index);
                if(option==null) continue;
                String id=option.optString("id");
                if(id.isEmpty()) continue;
                final String optionId=id;
                String icon=option.optString("icon");
                String title=option.optString("title");
                String description=option.optString("description");
                android.widget.LinearLayout row=new android.widget.LinearLayout(current);
                row.setOrientation(android.widget.LinearLayout.VERTICAL);
                row.setGravity(android.view.Gravity.CENTER_VERTICAL);
                int rowPad=Math.round(10*current.getResources().getDisplayMetrics().density);
                row.setPadding(rowPad,rowPad,rowPad,rowPad);
                row.setBackgroundResource(android.R.drawable.list_selector_background);
                // The dimension badge, then the name, then the effect: three lines
                // so a three-option dialog can be scanned without reading prose.
                android.widget.TextView text=new android.widget.TextView(current);
                String head=icon.isEmpty()?title:(icon+"  "+title);
                text.setText(head+"\n"+description);
                text.setTextSize(15);
                text.setTextColor(0xff161616);
                // Descriptions carry the whole promise, so they are the one part
                // that must never be clipped: a buff whose effect is cut off is
                // exactly the "no fake options" defect the spec forbids.
                text.setMaxLines(4);
                row.addView(text,new android.widget.LinearLayout.LayoutParams(
                    android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                    android.widget.LinearLayout.LayoutParams.WRAP_CONTENT));
                row.setOnClickListener((android.view.View v)->decide(handler,autoPick,decided,current,generation,optionId,title));
                list.addView(row,new android.widget.LinearLayout.LayoutParams(
                    android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                    android.widget.LinearLayout.LayoutParams.WRAP_CONTENT));
            }
            if(list.getChildCount()==0) return;
            android.widget.TextView countdown=null;
            if(timeoutSeconds>0) {
                countdown=new android.widget.TextView(current);
                countdown.setTextSize(13);
                countdown.setTextColor(0xff5a5a5a);
                countdown.setGravity(android.view.Gravity.CENTER);
                countdown.setPadding(0,0,0,pad);
                list.addView(countdown,new android.widget.LinearLayout.LayoutParams(
                    android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                    android.widget.LinearLayout.LayoutParams.WRAP_CONTENT));
            }
            AlertDialog dialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("【强化】第 "+waveIndex+" 波奖励 · 三选一")
                .setView(list)
                .setCancelable(false)
                .create();
            draftDialog=dialog;
            // The countdown is the safety net, not the primary path: it is there
            // so a run cannot wedge on a draft the player ignored, and it reports
            // the same choice a tap would so the two paths are indistinguishable
            // to the Python state machine. The visible readout is what makes the
            // fifteen seconds a promise the player can rely on rather than a
            // timer they have to guess at.
            autoPick[0]=()->decide(handler,autoPick,decided,current,generation,
                null,"超时自动选择");
            final android.widget.TextView readout=countdown;
            if(timeoutSeconds>0) {
                final int limit=Math.max(1,timeoutSeconds);
                final int[] remaining={limit};
                if(readout!=null) readout.setText(draftCountdownText(limit));
                // One ticker drives the visible seconds and the auto-pick is a
                // single delayed post, so the two can never disagree: the readout
                // hits 0 on the same callback that resolves the draft.
                final Runnable[] ticker=new Runnable[1];
                ticker[0]=()->{
                    if(decided[0]) return;
                    remaining[0]--;
                    if(readout!=null) readout.setText(draftCountdownText(Math.max(0,remaining[0])));
                    if(remaining[0]>0) handler.postDelayed(ticker[0],1000);
                };
                handler.postDelayed(ticker[0],1000);
                handler.postDelayed(autoPick[0],limit*1000L);
                dialog.setOnDismissListener(d->{
                    handler.removeCallbacks(autoPick[0]);
                    handler.removeCallbacks(ticker[0]);
                    if(draftDialog==dialog) draftDialog=null;
                });
            } else {
                dialog.setOnDismissListener(d->{
                    handler.removeCallbacks(autoPick[0]);
                    if(draftDialog==dialog) draftDialog=null;
                });
            }
            dialog.show();
        });
    }

    /** The countdown line under the three options. */
    private static String draftCountdownText(int seconds) {
        return seconds>0
            ? ("⏳ "+seconds+" 秒后自动选择第 1 项")
            : "⏳ 正在结算…";
    }

    /**
     * Commit a draft pick, at most once.
     *
     * <p>{@code optionId} null means the countdown fired, in which case the first
     * option is taken. The callback is dispatched to the worker thread because
     * it re-enters the Python runner, which is already driving this match.
     */
    private static void decide(android.os.Handler handler, Runnable[] autoPick, boolean[] decided,
                               Activity current, long generation,
                               String optionId, String title) {
        if(decided[0]) return;
        decided[0]=true;
        if(autoPick[0]!=null) handler.removeCallbacks(autoPick[0]);
        String picked=optionId;
        if(picked==null) picked=firstDraftOption();
        if(draftDialog!=null && draftDialog.isShowing()) {
            final AlertDialog showing=draftDialog;
            current.runOnUiThread(()->showing.dismiss());
        }
        if(!picked.isEmpty()) {
            Toast.makeText(current,"【强化】"+title,Toast.LENGTH_SHORT).show();
        }
        final String reported=picked;
        // NOT dispatched to `worker`: the match loop itself is running there
        // (run_device_match), and a single-thread executor would queue this pick
        // behind the very loop that is waiting for it -- a deadlock that reads as
        // "the draft dialog auto-picks every time". The mailbox write is a
        // volatile-store pair that the loop reads, so calling it inline from the
        // UI thread is both safe and immediate.
        reportRoguelikeChoice(generation,reported);
    }

    /** The id of the first option the current draft dialog is showing. */
    private static String firstDraftOption() {
        if(draftOptions==null || draftOptions.length()==0) return "";
        try {
            JSONObject first=draftOptions.getJSONObject(0);
            return first==null?"":first.optString("id");
        } catch (org.json.JSONException ignored) {
            return "";
        }
    }

    private static volatile JSONArray draftOptions;

    /**
     * Stamp the "AI 队友助战" choice onto the deck payload.
     *
     * <p>The flag only matters for a roguelike climb, and only there does
     * create_match read it, so the deck is returned unchanged for every other
     * mode. A deck that does not parse is passed straight through: create_match
     * is the authority on what a valid deck looks like and will report the real
     * problem, whereas throwing here would replace that message with this one.
     */
    private static String withAiAlly(String deck,Context context) {
        boolean ally=GameModePicker.aiAllyEnabled(context);
        try {
            JSONObject payload=new JSONObject(deck==null?"{}":deck);
            payload.put("aiAlly",ally);
            return payload.toString();
        } catch (org.json.JSONException malformed) {
            Log.w(TAG,"Deck payload is not a JSON object; ally flag not applied");
            return deck;
        }
    }

    /** Report a draft pick back to the Python runner that owns the buff state. */
    private static void reportRoguelikeChoice(long generation,String optionId) {
        try {
            if(module!=null) {
                module.get("report_draft_choice").call(generation,optionId);
            }
        } catch (Exception failure) {
            Log.w(TAG,"Roguelike draft choice was not accepted: "+failure);
        }
    }

    public static void showResult(long generation,int winner) {
        // One dialog per battle. The native bridge raises this when the engine
        // finalizes, which for a scripted defence happens after the scripted
        // dialog was already raised and dismissed; without this the second call
        // lands on the lobby and re-traps the player.
        if(generation>0 && resultShown) return;
        if(generation>0 && winner!=-2 && winner!=-3) { endedGeneration=generation; resultShown=true; }
        showResultForced(generation,winner);
    }

    /**
     * Raise the result dialog unconditionally.
     *
     * Separate from showResult so the scripted path can replace a dialog the
     * bridge already raised: the once-per-battle guard belongs to the bridge's
     * call, not to the mode's own verdict.
     */
    private static void showResultForced(long generation,int winner) {
        showResultForced(generation,winner,false);
    }

    private static void showResultForced(long generation,int winner,boolean replace) {
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        current.runOnUiThread(() -> {
            if(current.isFinishing() || current.isDestroyed()) return;
            if(generation!=0 && (generation!=activeGeneration || generation==abandonedGeneration)) return;
            // Replacing means the scripted verdict supersedes a dialog the
            // bridge already put up. It has to go first, otherwise the guard
            // below silently drops the authoritative verdict and the player
            // keeps reading the engine's duel result.
            if(replace && resultDialog!=null && resultDialog.isShowing()) {
                resultDialog.dismiss();
                resultDialog=null;
            }
            if(resultDialog!=null && resultDialog.isShowing()) return;
            if(leaveDialog!=null) { leaveDialog.dismiss(); leaveDialog=null; }
            // A scripted mode's own verdict wins over the engine's duel verdict.
            if(generation>0) {
                int scriptedWinner;
                String scriptedTitle;
                String scriptedMessage;
                synchronized(RESULT_LOCK) {
                    if(scriptedResultGeneration==generation) {
                        scriptedWinner=scriptedResultWinner;
                        scriptedTitle=scriptedResultTitle;
                        scriptedMessage=scriptedResultMessage;
                        // Consumed: one verdict per match.
                        scriptedResultGeneration=0;
                        scriptedResultTitle=null;
                        scriptedResultMessage=null;
                    } else {
                        scriptedWinner=Integer.MIN_VALUE;
                        scriptedTitle=null;
                        scriptedMessage=null;
                    }
                }
                if(scriptedWinner!=Integer.MIN_VALUE) {
                    resultDialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert)
                        .setTitle(scriptedTitle).setMessage(scriptedMessage).setCancelable(false)
                        .setPositiveButton(generation==0?"确定":"返回大厅",(dialog,which)-> {
                            if(generation!=0) { activeGeneration=0; nativeReturnHome(); }
                        })
                        .setOnDismissListener(d->resultDialog=null)
                        .show();
                    return;
                }
            }
            String title=winner==-3?"卡组未完成":winner==-2?"本地对局暂时无法继续":winner==0?"胜利":winner==1?"对局结束":"平局";
            String message=winner==-3?"请在卡组中配置八张不同的卡牌。":winner==-2?"运行记录已保存在设备日志中。":"离线对局已结束。";
            resultDialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert).setTitle(title).setMessage(message).setCancelable(false)
                .setPositiveButton(generation==0?"确定":"返回大厅",(dialog,which) -> {
                    if(generation!=0) { activeGeneration=0; nativeReturnHome(); }
                })
                .setOnDismissListener(d->resultDialog=null)
                .show();
        });
    }

    /** Android Back leaves an offline battle without asking a server to close it. */
    public static boolean onBackPressed() {
        long generation=activeGeneration;
        Activity current=activity;
        if(generation==0 || current==null || current.isFinishing() || current.isDestroyed()) return false;
        if(resultDialog!=null && resultDialog.isShowing() || leaveDialog!=null && leaveDialog.isShowing()) return true;
        leaveDialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert)
            .setTitle("退出本局？").setMessage("返回大厅后可以选择 AI 并开始新对局。")
            .setNegativeButton("继续对局",null)
            .setPositiveButton("返回大厅",(dialog,which) -> {
                abandonedGeneration=generation; activeGeneration=0; nativeReturnHome();
            })
            .setOnDismissListener(d->leaveDialog=null)
            .show();
        return true;
    }

    public static void recordNativePlay(long generation,int owner,int tick,int cardId) {
        Log.i(TAG,"Native card consumed generation="+generation+" owner="+owner+" tick="+tick+" card="+cardId);
    }

    public static void showOfflineFeatureNotice() {
        Activity current=activity;
        if(current!=null && !current.isFinishing() && !current.isDestroyed())
            current.runOnUiThread(() -> {
                if(!current.isFinishing() && !current.isDestroyed())
                    android.widget.Toast.makeText(current,
                        "离线版支持卡组编辑和 AI 对战",android.widget.Toast.LENGTH_SHORT).show();
            });
    }

    public static void updatePlayerDeck(String deck) { AiDeckSettings.updatePlayerDeck(deck); }

    /** Called with a fresh immutable deck from the native Decks button. */
    public static void useCurrentDeckForAllAi(String deck) {
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        current.runOnUiThread(() -> {
            if(current.isFinishing() || current.isDestroyed()) return;
            String message;
            try {
                AiOpponentPicker.Catalog catalog=AiOpponentPicker.readCatalog(current.getAssets());
                boolean saved=AiDeckSettings.saveForAll(current,catalog,deck);
                message=saved?"全部 AI 已使用当前卡组。":"保存失败，请重试";
                if(saved) Log.i(TAG,"AI deck saved for all models count="+catalog.choices.size()+
                    " deck="+AiDeckSettings.savedDeck(current,catalog.defaultId));
            } catch(org.json.JSONException invalid) {
                message="请先配置完整的八张牌和塔楼部署。";
            } catch(Exception error) {
                Log.e(TAG,"Saving deck for all AI failed",error);
                message="保存失败，请重试";
            }
            android.widget.Toast.makeText(current,message,android.widget.Toast.LENGTH_SHORT).show();
        });
    }

    private static SharedPreferences profilePreferences() {
        return application.getSharedPreferences("firstlight_profile_v1",Context.MODE_PRIVATE);
    }

    private static String playerName() {
        return profilePreferences().getString("player_name","Local Human");
    }

    private static String withPlayerName(String fixture,String name) throws org.json.JSONException {
        JSONObject json=new JSONObject(fixture);
        json.getJSONObject("battle").getJSONObject("avatar0").put("name",name);
        return json.toString();
    }

    /** Settings rename is local: persist first, then update the live native profile. */
    public static void showRenamePlayer() {
        Activity current=activity;
        if(current==null || current.isFinishing() || current.isDestroyed()) return;
        current.runOnUiThread(() -> {
            if(current.isFinishing() || current.isDestroyed()) return;
            if(activeGeneration!=0 || renameDialog!=null && renameDialog.isShowing()) return;
            EditText input=new EditText(current);
            input.setSingleLine(true);
            input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS);
            input.setText(playerName());
            input.setSelectAllOnFocus(true);
            int inset=(int)(24*current.getResources().getDisplayMetrics().density);
            android.widget.FrameLayout container=new android.widget.FrameLayout(current);
            container.setPadding(inset,0,inset,0);
            container.addView(input);
            AlertDialog dialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle("修改昵称").setMessage("昵称会保存在本机，用于大厅和对局。")
                .setView(container).setNegativeButton("取消",null).setPositiveButton("保存",null).create();
            renameDialog=dialog;
            dialog.setOnDismissListener(d->renameDialog=null);
            dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
                String name=input.getText().toString().trim();
                boolean valid=!name.isEmpty() && name.codePointCount(0,name.length())<=16;
                for(int i=0;i<name.length();i++) if(Character.isISOControl(name.charAt(i))) valid=false;
                if(!valid) { input.setError("请输入 1–16 个字符的昵称"); return; }
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(false);
                dialog.setCancelable(false);
                worker.execute(() -> {
                    try {
                        String updated=withPlayerName(homeFixture,name);
                        if(!profilePreferences().edit().putString("player_name",name).commit())
                            throw new java.io.IOException("Could not save local profile");
                        homeFixture=updated;
                        nativeBootReady(updated);
                        nativeRenamePlayer(name);
                        current.runOnUiThread(() -> {
                            if(!current.isFinishing() && !current.isDestroyed()) dialog.dismiss();
                        });
                        Log.i(TAG,"Local player name saved");
                    } catch(Throwable error) {
                        Log.e(TAG,"Local rename failed",error);
                        current.runOnUiThread(() -> {
                            if(!current.isFinishing() && !current.isDestroyed()) {
                                input.setError("保存失败，请重试");
                                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(true);
                                dialog.setCancelable(true);
                            }
                        });
                    }
                });
            }));
            dialog.show();
        });
    }

    private static native void nativeAttach();
    private static native void nativeBootReady(String fixture);
    private static native void nativeRenamePlayer(String name);
    private static native void nativeSubmitMatch(long request,String fixture);
    public static native String nextFrame(long generation,int tick);
    public static native String queueAction(long generation,int observed,int due,int hand,int expectedCard,int x,int y,
                                           boolean ability,int objectIndex,int secondaryIndex);
    public static native String queueActionForOwner(long generation,int observed,int due,int hand,int expectedCard,int x,int y,
                                                   boolean ability,int objectIndex,int secondaryIndex,int owner);
    // PvE wave path: arms a card deploy for an absolute future tick, resolved by
    // card id rather than a hand slot and without an elixir charge, so a scripted
    // wave table can be scheduled well ahead of the tick it fires on.
    //
    // `level` is the character level the unit is built at, 1..15, which is what
    // makes a wave table's level column real: the monsters climb from level 1 at
    // the first wave to level 15 at the finale. Pass 0 to keep the deck's own
    // level, which is what every non-PvE caller wants; the runtime refuses
    // anything else rather than clamping, so a table bug cannot pass silently.
    public static native String scheduleCard(int owner,int cardId,int x,int y,int executeTick,int level);
    // PvE buff path: apply a seat's elixir-rate buff, 1.0..4.0. Called once per
    // frame while a rate buff is in effect, because the engine recomputes elixir
    // every tick and a one-shot credit would be spent immediately.
    public static native String setPveElixirRate(int owner,float multiplier);
    private static native void nativeReturnHome();
    private static native void nativeAbort(long generation);
    private static native void nativeSetForeground(boolean foreground);
}
