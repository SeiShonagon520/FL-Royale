package com.firstlight.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;

/** An independent, persistent copy of a deck edited in the native Collection. */
public final class AiDeckSettings {
    private static final String PREFS="firstlight_ai_decks_v1";
    private static volatile String playerDeck="";
    private AiDeckSettings() {}

    public static void updatePlayerDeck(String value) { playerDeck=value; }
    public static String savedDeck(Context context,String id) {
        return context.getSharedPreferences(PREFS,Context.MODE_PRIVATE).getString(id,"");
    }
    private static String equipmentJson(String value) throws org.json.JSONException {
        JSONObject deck=new JSONObject(value);
        JSONArray cards=deck.getJSONArray("cards");
        if(cards.length()!=8 || deck.getJSONArray("active").length()!=8)
            throw new org.json.JSONException("Incomplete deck");
        for(int i=0;i<8;i++) for(int j=0;j<i;j++)
            if(cards.getInt(i)==cards.getInt(j)) throw new org.json.JSONException("Duplicate card");
        deck.getInt("tower");
        deck.remove("cosmetics");
        deck.remove("slot");
        return deck.toString();
    }
    /** Snapshot the native editor once and persist the same equipment for every model. */
    public static boolean saveForAll(Context context,AiOpponentPicker.Catalog catalog,String value)
            throws org.json.JSONException {
        String equipment=equipmentJson(value);
        SharedPreferences.Editor edit=context.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit();
        for(AiOpponentPicker.Choice choice:catalog.choices) edit.putString(choice.id,equipment);
        return edit.commit();
    }
    private static String summary(String value) {
        if(value.isEmpty()) return "卡组未完成，需要八张不同的卡牌";
        try {
            JSONObject deck=new JSONObject(value);
            JSONArray names=deck.getJSONArray("names"), forms=deck.getJSONArray("active");
            StringBuilder text=new StringBuilder();
            for(int i=0;i<names.length();i++) {
                if(i>0) text.append(i%2==0?"\n":" · ");
                text.append(names.getString(i));
                if(forms.getInt(i)==1) text.append("〔觉醒〕");
                if(forms.getInt(i)==2) text.append("〔英雄〕");
            }
            int tower=deck.optInt("tower",159000000);
            text.append("\n塔楼部队：").append(tower==159000001?"加农炮手":tower==159000002?"飞刀女爵":
                tower==159000004?"皇家厨师":"公主塔");
            return text.toString();
        } catch(Exception invalid) { return "卡组不可用，请重新保存"; }
    }
    public static void show(Activity activity,AiOpponentPicker.Catalog catalog) {
        String[] labels=new String[catalog.choices.size()];
        for(int i=0;i<labels.length;i++) {
            AiOpponentPicker.Choice choice=catalog.choices.get(i);
            labels[i]=choice.label+(savedDeck(activity,choice.id).isEmpty()?" · 默认卡组":" · 已保存独立卡组");
        }
        new AlertDialog.Builder(activity,android.R.style.Theme_Material_Light_Dialog_Alert)
            .setTitle("配置 AI 卡组")
            .setItems(labels,(dialog,index) -> edit(activity,catalog.choices.get(index)))
            .setNegativeButton("关闭",null).show();
    }
    private static void edit(Activity activity,AiOpponentPicker.Choice choice) {
        final String current=playerDeck;
        String saved=savedDeck(activity,choice.id);
        String fallback=choice.id.startsWith("hog26_")?"默认 2.6 猪卡组":"默认跟随玩家卡组";
        String message="AI 当前使用：\n"+(saved.isEmpty()?fallback:summary(saved))+
            "\n\n当前玩家卡组：\n"+summary(current)+
            "\n\n在 Collection → Decks 编辑卡组，然后在这里保存给 AI。保存后可切回玩家卡组，AI 的八卡、形态和塔楼部队会独立保留。";
        AlertDialog dialog=new AlertDialog.Builder(activity,android.R.style.Theme_Material_Light_Dialog_Alert)
            .setTitle(choice.label+" · AI 卡组").setMessage(message)
            .setPositiveButton("保存当前卡组给 AI",(ignored,which) -> {
                String equipment;
                try {
                    equipment=equipmentJson(current);
                } catch(Exception invalid) { return; }
                boolean ok=activity.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit().putString(choice.id,equipment).commit();
                Toast.makeText(activity,ok?"AI 卡组已独立保存":"保存失败，请重试",Toast.LENGTH_SHORT).show();
            })
            .setNeutralButton("恢复默认",(ignored,which) -> {
                boolean ok=activity.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit().remove(choice.id).commit();
                Toast.makeText(activity,ok?"已恢复默认卡组":"保存失败，请重试",Toast.LENGTH_SHORT).show();
            }).setNegativeButton("取消",null).create();
        dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(!current.isEmpty());
    }
}
