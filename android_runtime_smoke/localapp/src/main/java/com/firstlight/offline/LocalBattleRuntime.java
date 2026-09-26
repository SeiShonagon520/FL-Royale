package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.text.InputType;
import android.util.Log;
import android.widget.EditText;
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
    private static String matchConfiguration;
    private static String homeFixture;
    private static volatile long activeGeneration, abandonedGeneration, endedGeneration;
    private static AlertDialog resultDialog, leaveDialog, renameDialog;

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
            public void onActivityDestroyed(Activity value) {}
        });
        Context app=context.getApplicationContext();
        application=app;
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
                String fixture=module.callAttr("create_match","[26000000,26000001,26000005,28000001,28000000,26000003,26000014,26000018]","general").toString();
                homeFixture=withPlayerName(fixture,playerName());
                nativeBootReady(homeFixture);
                Log.i(TAG,"Offline runtime ready ms="+(System.nanoTime()-began)/1e6);
            } catch(Throwable error) { Log.e(TAG,"Offline bootstrap failed",error); showResult(0,-2); }
        });
    }

    public static void prepareMatch(long request,String aiId,String deck) {
        worker.execute(() -> {
            try {
                GameOrtBootstrap.PolicyLease selected=GameOrtBootstrap.acquire();
                if(selected.policy==null || !aiId.equals(selected.aiId) || !GameOrtBootstrap.isCurrent(selected))
                    throw new IllegalStateException("Selected policy changed");
                String opponent=AiDeckSettings.savedDeck(activity,aiId);
                String fixture=withPlayerName(module.callAttr("create_match",deck,aiId,opponent).toString(),playerName());
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

    public static void onNativeBattleCreated(long generation) {
        activeGeneration=generation;
        worker.execute(() -> {
            try {
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
            } finally { if(recurrent!=null) recurrent.abortBattle(generation); recurrent=null; lease=null; }
        });
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

    public static void showResult(long generation,int winner) {
        if(generation>0 && winner!=-2 && winner!=-3) endedGeneration=generation;
        Activity current=activity;
        if(current==null || current.isFinishing()) return;
        current.runOnUiThread(() -> {
            if(generation!=0 && (generation!=activeGeneration || generation==abandonedGeneration)) return;
            if(resultDialog!=null && resultDialog.isShowing()) return;
            if(leaveDialog!=null) leaveDialog.dismiss();
            String title=winner==-3?"卡组未完成":winner==-2?"本地对局暂时无法继续":winner==0?"胜利":winner==1?"对局结束":"平局";
            String message=winner==-3?"请在卡组中配置八张不同的卡牌。":winner==-2?"运行记录已保存在设备日志中。":"离线对局已结束。";
            resultDialog=new AlertDialog.Builder(current,android.R.style.Theme_Material_Light_Dialog_Alert).setTitle(title).setMessage(message).setCancelable(false)
                .setPositiveButton(generation==0?"确定":"返回大厅",(dialog,which) -> {
                    if(generation!=0) { activeGeneration=0; nativeReturnHome(); }
                }).show();
        });
    }

    /** Android Back leaves an offline battle without asking a server to close it. */
    public static boolean onBackPressed() {
        long generation=activeGeneration;
        if(generation==0 || activity==null) return false;
        if(resultDialog!=null && resultDialog.isShowing() || leaveDialog!=null && leaveDialog.isShowing()) return true;
        leaveDialog=new AlertDialog.Builder(activity,android.R.style.Theme_Material_Light_Dialog_Alert)
            .setTitle("退出本局？").setMessage("返回大厅后可以选择 AI 并开始新对局。")
            .setNegativeButton("继续对局",null)
            .setPositiveButton("返回大厅",(dialog,which) -> {
                abandonedGeneration=generation; activeGeneration=0; nativeReturnHome();
            }).show();
        return true;
    }

    public static void recordNativePlay(long generation,int owner,int tick,int cardId) {
        Log.i(TAG,"Native card consumed generation="+generation+" owner="+owner+" tick="+tick+" card="+cardId);
    }

    public static void showOfflineFeatureNotice() {
        Activity current=activity;
        if(current!=null) current.runOnUiThread(() -> android.widget.Toast.makeText(current,
            "离线版支持卡组编辑和 AI 对战",android.widget.Toast.LENGTH_SHORT).show());
    }

    public static void updatePlayerDeck(String deck) { AiDeckSettings.updatePlayerDeck(deck); }

    /** Called with a fresh immutable deck from the native Decks button. */
    public static void useCurrentDeckForAllAi(String deck) {
        Activity current=activity;
        if(current==null || current.isFinishing()) return;
        current.runOnUiThread(() -> {
            String message;
            try {
                AiOpponentPicker.Catalog catalog=AiOpponentPicker.readCatalog(current.getAssets());
                boolean saved=AiDeckSettings.saveForAll(current,catalog,deck);
                message=saved?"全部 AI 已使用当前卡组":"保存失败，请重试";
                if(saved) Log.i(TAG,"AI deck saved for all models count="+catalog.choices.size()+
                    " deck="+AiDeckSettings.savedDeck(current,catalog.defaultId));
            } catch(org.json.JSONException invalid) {
                message="请先配置完整的八张牌和塔楼部队";
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
        if(current==null || current.isFinishing()) return;
        current.runOnUiThread(() -> {
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
                        current.runOnUiThread(dialog::dismiss);
                        Log.i(TAG,"Local player name saved");
                    } catch(Throwable error) {
                        Log.e(TAG,"Local rename failed",error);
                        current.runOnUiThread(() -> {
                            input.setError("保存失败，请重试");
                            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(true);
                            dialog.setCancelable(true);
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
    private static native void nativeReturnHome();
    private static native void nativeAbort(long generation);
    private static native void nativeSetForeground(boolean foreground);
}
