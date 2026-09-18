package com.example.androidagentbridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.charset.Charset

class AgentAccessibilityService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())
    private val uiFile = File("/sdcard/ui_state.json")
    private val commandFile = File("/sdcard/agent_command.json")
    private val resultFile = File("/sdcard/agent_result.json")
    private val poller = object : Runnable {
        override fun run() {
            try {
                if (commandFile.exists()) {
                    val raw = try {
                        commandFile.readText(Charset.forName("UTF-8")).trim()
                    } catch (_: Exception) { "" }

                    if (raw.isNotEmpty()) {
                        try {
                            val command = JSONObject(raw)
                            executeCommand(command)
                            try { commandFile.delete() } catch (_: Exception) {}
                        } catch (_: org.json.JSONException) {
                            // JSON غير مكتمل، نحاول بالدورة القادمة
                        }
                    }
                }
            } catch (e: Exception) {
                writeResult(false, e.message ?: "poller error")
            }
            handler.postDelayed(this, 300)
        }
    }
        private val dumpRunnable = Runnable {
        dumpUi()
    }

    override fun onServiceConnected() { super.onServiceConnected(); handler.post(poller); dumpUi() }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return

        val eventPackage = event.packageName?.toString() ?: ""

        try {
            File("/sdcard/accessibility_events.log").appendText(
                "${System.currentTimeMillis()} type=${event.eventType} package=$eventPackage
",
                Charset.forName("UTF-8")
            )
        } catch (_: Exception) {}

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                handler.removeCallbacks(dumpRunnable)
                dumpUi()
            }
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
            AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED,
            AccessibilityEvent.TYPE_VIEW_CLICKED,
            AccessibilityEvent.TYPE_VIEW_SCROLLED -> {
                if (!handler.hasCallbacks(dumpRunnable)) {
                    handler.postDelayed(dumpRunnable, 150)
                }
            }
        }
    }
    override fun onInterrupt() {}
    override fun onDestroy() { handler.removeCallbacks(poller); super.onDestroy() }

    private fun dumpUi() {
        try {
            val root = rootInActiveWindow ?: return
            val packageName = root.packageName?.toString() ?: ""
            val out = JSONObject().put("timestamp", System.currentTimeMillis()).put("package", packageName)
            val nodes = JSONArray()
            walk(root, nodes)
            out.put("elements", nodes)
            uiFile.writeText(out.toString(2), Charset.forName("UTF-8"))
            root.recycle()
        } catch (_: Exception) {}
    }
    private fun walk(node: AccessibilityNodeInfo,out:JSONArray) {
        val r=Rect(); node.getBoundsInScreen(r)
        val item=JSONObject().put("id",out.length()).put("text",node.text?.toString() ?: "").put("content_desc",node.contentDescription?.toString() ?: "").put("resource_id",node.viewIdResourceName ?: "").put("class",node.className?.toString() ?: "").put("package",node.packageName?.toString() ?: "").put("clickable",node.isClickable).put("scrollable",node.isScrollable).put("enabled",node.isEnabled).put("focused",node.isFocused).put("bounds",JSONArray().put(r.left).put(r.top).put(r.right).put(r.bottom)); out.put(item)
        for(i in 0 until node.childCount){ node.getChild(i)?.let{ child -> walk(child,out); child.recycle() } }
    }

    private fun executeCommand(cmd: JSONObject) {
        when(cmd.optString("action")) {
            "dump" -> { dumpUi(); writeResult(true,"dump") }
            "tap" -> {
                val id=cmd.optInt("element_id",-1); val node=if(id>=0) findByIndex(id) else null
                val ok=if(node!=null && node.isClickable) node.performAction(AccessibilityNodeInfo.ACTION_CLICK) else tap(cmd.optDouble("x",-1.0).toFloat(),cmd.optDouble("y",-1.0).toFloat())
                node?.recycle(); writeResult(ok,"tap")
            }
            "swipe" -> writeResult(swipe(cmd.optDouble("x1").toFloat(),cmd.optDouble("y1").toFloat(),cmd.optDouble("x2").toFloat(),cmd.optDouble("y2").toFloat(),cmd.optLong("duration_ms",400)),"swipe")
            "type" -> {
                val node=findByIndex(cmd.optInt("element_id",-1)); val ok=node?.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,Bundle().apply{putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,cmd.optString("text"))}) ?: false
                node?.recycle(); writeResult(ok,"type")
            }
            "back" -> writeResult(performGlobalAction(GLOBAL_ACTION_BACK),"back")
            "home" -> writeResult(performGlobalAction(GLOBAL_ACTION_HOME),"home")
            else -> writeResult(false,"unknown action")
        }
        handler.postDelayed({dumpUi()},250)
    }
    private fun findByIndex(target:Int):AccessibilityNodeInfo? { val root=rootInActiveWindow ?: return null; val c=intArrayOf(0); val f=findRecursive(root,target,c); root.recycle(); return f }
    private fun findRecursive(n:AccessibilityNodeInfo,target:Int,c:IntArray):AccessibilityNodeInfo? { if(c[0]==target)return AccessibilityNodeInfo.obtain(n); c[0]++; for(i in 0 until n.childCount){val ch=n.getChild(i)?:continue; val f=findRecursive(ch,target,c); ch.recycle(); if(f!=null)return f}; return null }
    private fun tap(x:Float,y:Float):Boolean { if(x<0||y<0)return false; val p=Path().apply{moveTo(x,y)}; return dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(p,0,60)).build(),null,null) }
    private fun swipe(x1:Float,y1:Float,x2:Float,y2:Float,d:Long):Boolean { val p=Path().apply{moveTo(x1,y1);lineTo(x2,y2)}; return dispatchGesture(GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(p,0,d.coerceIn(50,5000))).build(),null,null) }
    private fun writeResult(ok:Boolean,msg:String){try{resultFile.writeText(JSONObject().put("ok",ok).put("message",msg).put("timestamp",System.currentTimeMillis()).toString(2),Charset.forName("UTF-8"))}catch(_:Exception){}}
}
