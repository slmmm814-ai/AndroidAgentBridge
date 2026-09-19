package com.example.androidagentbridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
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

    @Volatile
    private var targetPackage: String = ""

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
                        } catch (_: org.json.JSONException) {}
                    }
                }
            } catch (e: Exception) {
                writeResult(false, e.message ?: "poller error")
            }
            handler.postDelayed(this, 300)
        }
    }

    private val dumpRunnable = Runnable { dumpUi() }

    override fun onServiceConnected() {
        super.onServiceConnected()
        handler.post(poller)
        dumpUi()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
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

    override fun onDestroy() {
        handler.removeCallbacks(poller)
        handler.removeCallbacks(dumpRunnable)
        super.onDestroy()
    }

    private fun dumpUi(preferredPackage: String = "") {
        try {
            val lockedPackage = targetPackage

            if (lockedPackage.isNotEmpty()) {
                val activeRoot = rootInActiveWindow
                if (activeRoot != null) {
                    val activePackage = activeRoot.packageName?.toString() ?: ""
                    if (activePackage == lockedPackage) {
                        writeUiRoot(activeRoot)
                        activeRoot.recycle()
                        return
                    }
                    activeRoot.recycle()
                }
            }

            var activeFocusedRoot: AccessibilityNodeInfo? = null
            var activeRoot2: AccessibilityNodeInfo? = null
            var focusedRoot: AccessibilityNodeInfo? = null
            var preferredRoot: AccessibilityNodeInfo? = null

            for (window in windows) {
                try {
                    val root = window.root ?: continue
                    val pkg = root.packageName?.toString() ?: ""

                    if (lockedPackage.isNotEmpty() && pkg != lockedPackage) {
                        root.recycle()
                        continue
                    }

                    if (preferredPackage.isNotEmpty() && pkg == preferredPackage && preferredRoot == null) {
                        preferredRoot = AccessibilityNodeInfo.obtain(root)
                    }

                    if (window.isActive && window.isFocused) {
                        activeFocusedRoot = AccessibilityNodeInfo.obtain(root)
                        root.recycle()
                        break
                    }
                    if (window.isActive && activeRoot2 == null) {
                        activeRoot2 = AccessibilityNodeInfo.obtain(root)
                    }
                    if (window.isFocused && focusedRoot == null) {
                        focusedRoot = AccessibilityNodeInfo.obtain(root)
                    }
                    root.recycle()
                } catch (_: Exception) {}
            }

            val selectedRoot = preferredRoot ?: activeFocusedRoot ?: activeRoot2 ?: focusedRoot

            if (selectedRoot != null) {
                writeUiRoot(selectedRoot)
                selectedRoot.recycle()
                return
            }

            if (lockedPackage.isNotEmpty()) {
                return
            }

            val root = rootInActiveWindow ?: return
            writeUiRoot(root)
            root.recycle()
        } catch (_: Exception) {}
    }

    private fun writeUiRoot(root: AccessibilityNodeInfo) {
        try {
            val packageName = root.packageName?.toString() ?: ""
            val lockedPackage = targetPackage
            if (lockedPackage.isNotEmpty() && packageName != lockedPackage) {
                return
            }
            val out = JSONObject().put("timestamp", System.currentTimeMillis()).put("package", packageName)
            val nodes = JSONArray()
            walk(root, nodes)
            out.put("elements", nodes)
            uiFile.writeText(out.toString(2), Charset.forName("UTF-8"))
        } catch (_: Exception) {}
    }

    private fun walk(node: AccessibilityNodeInfo, out: JSONArray) {
        val r = Rect()
        node.getBoundsInScreen(r)
        val item = JSONObject()
            .put("id", out.length())
            .put("text", node.text?.toString() ?: "")
            .put("content_desc", node.contentDescription?.toString() ?: "")
            .put("resource_id", node.viewIdResourceName ?: "")
            .put("class", node.className?.toString() ?: "")
            .put("package", node.packageName?.toString() ?: "")
            .put("clickable", node.isClickable)
            .put("scrollable", node.isScrollable)
            .put("enabled", node.isEnabled)
            .put("focused", node.isFocused)
            .put("bounds", JSONArray().put(r.left).put(r.top).put(r.right).put(r.bottom))
        out.put(item)
        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { child ->
                walk(child, out)
                child.recycle()
            }
        }
    }

    private fun executeCommand(cmd: JSONObject) {
        when (cmd.optString("action")) {
            "dump" -> {
                dumpUi()
                writeResult(true, "dump")
            }
            "tap" -> {
                val beforeRoot = rootInActiveWindow
                val beforePackage = beforeRoot?.packageName?.toString() ?: ""
                val beforeSignature = beforeRoot?.let { uiSignature(it) } ?: ""
                beforeRoot?.recycle()
                val id = cmd.optInt("element_id", -1)
                val node = if (id >= 0) findByIndex(id) else null
                val actionStarted = if (node != null && node.isClickable) {
                    node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                } else {
                    tap(cmd.optDouble("x", -1.0).toFloat(), cmd.optDouble("y", -1.0).toFloat())
                }
                node?.recycle()
                if (!actionStarted) {
                    writeResult(false, "tap execution failed")
                } else {
                    handler.postDelayed({ verifyTap(beforePackage, beforeSignature) }, 700)
                }
            }
            "type" -> {
                val elementId = cmd.optInt("element_id", -1)
                val text = cmd.optString("text", "")
                val node = findByIndex(elementId)
                val actionStarted = node?.performAction(
                    AccessibilityNodeInfo.ACTION_SET_TEXT,
                    Bundle().apply {
                        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                    }
                ) ?: false
                node?.recycle()
                if (!actionStarted) {
                    writeResult(false, "type execution failed")
                } else {
                    handler.postDelayed({ verifyType(text) }, 700)
                }
            }
            "swipe" -> {
                val beforeRoot = rootInActiveWindow
                val beforePackage = beforeRoot?.packageName?.toString() ?: ""
                val beforeSignature = beforeRoot?.let { uiSignature(it) } ?: ""
                beforeRoot?.recycle()
                val actionStarted = swipe(
                    cmd.optDouble("x1").toFloat(),
                    cmd.optDouble("y1").toFloat(),
                    cmd.optDouble("x2").toFloat(),
                    cmd.optDouble("y2").toFloat(),
                    cmd.optLong("duration_ms", 400)
                )
                if (!actionStarted) {
                    writeResult(false, "swipe execution failed")
                } else {
                    handler.postDelayed({ verifySwipe(beforePackage, beforeSignature) }, 700)
                }
            }
            "back" -> {
                targetPackage = ""
                val ok = performGlobalAction(GLOBAL_ACTION_BACK)
                writeResult(ok, "back")
            }
            "home" -> {
                targetPackage = ""
                val ok = performGlobalAction(GLOBAL_ACTION_HOME)
                writeResult(ok, "home")
            }
            "open_app" -> {
                val packageName = cmd.optString("package", "").trim()
                if (packageName.isEmpty()) {
                    writeResult(false, "missing package")
                } else {
                    try {
                        targetPackage = packageName
                        val intent = packageManager.getLaunchIntentForPackage(packageName)
                        if (intent == null) {
                            targetPackage = ""
                            writeResult(false, "no launcher activity: $packageName")
                        } else {
                            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            startActivity(intent)
                            handler.postDelayed({
                                try {
                                    val root = rootInActiveWindow
                                    val currentPackage = root?.packageName?.toString() ?: ""
                                    if (currentPackage == packageName) {
                                        if (root != null) {
                                            writeUiRoot(root)
                                        }
                                        writeResult(true, "open_app verified: $packageName")
                                    } else {
                                        writeResult(false, "open_app not verified: expected=$packageName current=$currentPackage")
                                        targetPackage = ""
                                    }
                                    root?.recycle()
                                } catch (e: Exception) {
                                    targetPackage = ""
                                    writeResult(false, "open_app verification failed: ${e.message}")
                                }
                            }, 1000)
                        }
                    } catch (e: Exception) {
                        targetPackage = ""
                        writeResult(false, "open_app failed: ${e.message}")
                    }
                }
            }
            else -> {
                writeResult(false, "unknown action")
            }
        }

        if (cmd.optString("action") != "open_app") {
            handler.postDelayed({ dumpUi() }, 250)
        }
    }

    private fun verifyTap(beforePackage: String, beforeSignature: String) {
        try {
            val root = rootInActiveWindow
            if (root == null) {
                writeResult(false, "tap verification failed: no active root")
                return
            }
            val afterPackage = root.packageName?.toString() ?: ""
            val afterSignature = uiSignature(root)
            val changed = afterPackage != beforePackage || afterSignature != beforeSignature
            if (changed) {
                writeResult(true, "tap verified")
            } else {
                writeResult(false, "tap not verified: UI did not change")
            }
            root.recycle()
        } catch (e: Exception) {
            writeResult(false, "tap verification failed: ${e.message}")
        }
    }

    private fun verifyType(expectedText: String) {
        try {
            val root = rootInActiveWindow
            if (root == null) {
                writeResult(false, "type verification failed: no active root")
                return
            }
            val found = containsText(root, expectedText)
            if (found) {
                writeResult(true, "type verified")
            } else {
                writeResult(false, "type not verified: text not found")
            }
            root.recycle()
        } catch (e: Exception) {
            writeResult(false, "type verification failed: ${e.message}")
        }
    }

    private fun verifySwipe(beforePackage: String, beforeSignature: String) {
        try {
            val root = rootInActiveWindow
            if (root == null) {
                writeResult(false, "swipe verification failed: no active root")
                return
            }
            val afterPackage = root.packageName?.toString() ?: ""
            val afterSignature = uiSignature(root)
            val changed = afterPackage != beforePackage || afterSignature != beforeSignature
            if (changed) {
                writeResult(true, "swipe verified")
            } else {
                writeResult(false, "swipe not verified: UI did not change")
            }
            root.recycle()
        } catch (e: Exception) {
            writeResult(false, "swipe verification failed: ${e.message}")
        }
    }

    private fun containsText(node: AccessibilityNodeInfo, expectedText: String): Boolean {
        val nodeText = node.text?.toString() ?: ""
        if (nodeText == expectedText) return true
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = containsText(child, expectedText)
            child.recycle()
            if (found) return true
        }
        return false
    }

    private fun uiSignature(root: AccessibilityNodeInfo): String {
        return try {
            val nodes = JSONArray()
            walk(root, nodes)
            nodes.toString()
        } catch (_: Exception) { "" }
    }

    private fun findByIndex(target: Int): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val counter = intArrayOf(0)
        val found = findRecursive(root, target, counter)
        root.recycle()
        return found
    }

    private fun findRecursive(node: AccessibilityNodeInfo, target: Int, counter: IntArray): AccessibilityNodeInfo? {
        if (counter[0] == target) return AccessibilityNodeInfo.obtain(node)
        counter[0]++
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findRecursive(child, target, counter)
            child.recycle()
            if (found != null) return found
        }
        return null
    }

    private fun tap(x: Float, y: Float): Boolean {
        if (x < 0 || y < 0) return false
        val path = Path().apply { moveTo(x, y) }
        return dispatchGesture(
            GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 60)).build(),
            null, null
        )
    }

    private fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        return dispatchGesture(
            GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, duration.coerceIn(50, 5000))).build(),
            null, null
        )
    }

    private fun writeResult(ok: Boolean, msg: String) {
        try {
            resultFile.writeText(
                JSONObject().put("ok", ok).put("message", msg).put("timestamp", System.currentTimeMillis()).toString(2),
                Charset.forName("UTF-8")
            )
        } catch (_: Exception) {}
    }
}
