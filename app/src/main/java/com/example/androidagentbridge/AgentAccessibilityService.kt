package com.example.androidagentbridge

import android.graphics.Bitmap
import android.os.Build
import java.io.FileOutputStream

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
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

    @Volatile
    private var currentCommandId: String = ""

    private val poller = object : Runnable {
        override fun run() {
            try {
                if (commandFile.exists()) {
                    val raw = try {
                        commandFile.readText(Charset.forName("UTF-8")).trim()
                    } catch (_: Exception) {
                        ""
                    }

                    if (raw.isNotEmpty()) {
                        try {
                            val command = JSONObject(raw)

                            currentCommandId =
                                command.optString("command_id", "")

                            executeCommand(command)

                            try {
                                commandFile.delete()
                            } catch (_: Exception) {}
                        } catch (_: org.json.JSONException) {}
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

    private fun dumpUi(preferredPackage: String = ""): Boolean {
        try {
            val lockedPackage = targetPackage

            if (lockedPackage.isNotEmpty()) {
                val activeRoot = rootInActiveWindow

                if (activeRoot != null) {
                    val activePackage =
                        activeRoot.packageName?.toString() ?: ""

                    if (activePackage == lockedPackage) {
                        writeUiRoot(activeRoot)
                        activeRoot.recycle()
                        return true
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

                    if (
                        preferredPackage.isNotEmpty() &&
                        pkg == preferredPackage &&
                        preferredRoot == null
                    ) {
                        preferredRoot = AccessibilityNodeInfo.obtain(root)
                    }

                    if (window.isActive && window.isFocused) {
                        activeFocusedRoot =
                            AccessibilityNodeInfo.obtain(root)

                        root.recycle()
                        break
                    }

                    if (window.isActive && activeRoot2 == null) {
                        activeRoot2 =
                            AccessibilityNodeInfo.obtain(root)
                    }

                    if (window.isFocused && focusedRoot == null) {
                        focusedRoot =
                            AccessibilityNodeInfo.obtain(root)
                    }

                    root.recycle()
                } catch (_: Exception) {}
            }

            val selectedRoot =
                preferredRoot
                    ?: activeFocusedRoot
                    ?: activeRoot2
                    ?: focusedRoot

            if (selectedRoot != null) {
                writeUiRoot(selectedRoot)
                selectedRoot.recycle()
                return true
            }

            if (lockedPackage.isNotEmpty()) {
                // التطبيق المقفول لم يعد له نافذة ظاهرة إطلاقًا.
                // بدل الفشل الصامت وترك ui_state.json عالقًا على بيانات
                // قديمة، نحرّر القفل تلقائيًا ونكتب حالة التطبيق
                // الحقيقي الظاهر فعليًا الآن — حتى لو لم يكن هذا هو
                // التطبيق الذي كان مستهدفًا سابقًا. هذا يضمن أن أي
                // طلب dump يعكس الواقع دائمًا، ولا يبقى عالقًا أبدًا.
                targetPackage = ""
            }

            val root = rootInActiveWindow ?: return false

            writeUiRoot(root)
            root.recycle()

            return true

        } catch (_: Exception) {
            return false
        }
    }

    private fun writeUiRoot(root: AccessibilityNodeInfo) {
        try {
            val packageName =
                root.packageName?.toString() ?: ""

            val lockedPackage = targetPackage

            if (
                lockedPackage.isNotEmpty() &&
                packageName != lockedPackage
            ) {
                return
            }

            val out = JSONObject()
                .put("timestamp", System.currentTimeMillis())
                .put("package", packageName)

            val nodes = JSONArray()

            walk(root, nodes)

            out.put("elements", nodes)

            uiFile.writeText(
                out.toString(2),
                Charset.forName("UTF-8")
            )

        } catch (_: Exception) {}
    }

    private fun walk(
        node: AccessibilityNodeInfo,
        out: JSONArray
    ) {
        val r = Rect()

        node.getBoundsInScreen(r)

        val item = JSONObject()
            .put("id", out.length())
            .put("text", node.text?.toString() ?: "")
            .put(
                "content_desc",
                node.contentDescription?.toString() ?: ""
            )
            .put(
                "resource_id",
                node.viewIdResourceName ?: ""
            )
            .put(
                "class",
                node.className?.toString() ?: ""
            )
            .put(
                "package",
                node.packageName?.toString() ?: ""
            )
            .put("clickable", node.isClickable)
            .put("scrollable", node.isScrollable)
            .put("enabled", node.isEnabled)
            .put("focused", node.isFocused)
            .put(
                "bounds",
                JSONArray()
                    .put(r.left)
                    .put(r.top)
                    .put(r.right)
                    .put(r.bottom)
            )

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
                val wrote = dumpUi()

                if (wrote) {
                    writeResult(true, "dump")
                } else {
                    writeResult(
                        false,
                        "dump failed: target package has no visible window"
                    )
                }
            }

            "tap" -> {
                executeTap(cmd)
            }

            "type" -> {
                executeType(cmd)
            }

            "swipe" -> {
                executeSwipe(cmd)
            }

            "scroll" -> {
                executeScroll(cmd)
            }

            "long_press" -> {
                executeLongPress(cmd)
            }

            "screenshot" -> {
                executeScreenshot()
            }

            "back" -> {
                targetPackage = ""

                val ok =
                    performGlobalAction(GLOBAL_ACTION_BACK)

                writeResult(ok, "back")
            }

            "home" -> {
                targetPackage = ""

                val ok =
                    performGlobalAction(GLOBAL_ACTION_HOME)

                writeResult(ok, "home")
            }

            "list_apps" -> {
                executeListApps()
            }

            "open_app" -> {
                executeOpenApp(cmd)
            }

            else -> {
                writeResult(false, "unknown action")
            }
        }

        if (cmd.optString("action") != "open_app") {
            handler.postDelayed(
                { dumpUi() },
                250
            )
        }
    }

    private fun executeScreenshot() {

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {

            writeResult(
                false,
                "screenshot requires Android 11 (API 30) or newer"
            )

            return
        }

        try {

            takeScreenshot(
                android.view.Display.DEFAULT_DISPLAY,
                mainExecutor,
                object : TakeScreenshotCallback {

                    override fun onSuccess(
                        screenshot: ScreenshotResult
                    ) {

                        try {

                            val hardwareBuffer =
                                screenshot.hardwareBuffer

                            val bitmap =
                                Bitmap.wrapHardwareBuffer(
                                    hardwareBuffer,
                                    screenshot.colorSpace
                                )

                            if (bitmap == null) {

                                hardwareBuffer.close()

                                writeResult(
                                    false,
                                    "screenshot failed: bitmap is null"
                                )

                                return
                            }

                            val outputBitmap =
                                bitmap.copy(
                                    Bitmap.Config.ARGB_8888,
                                    false
                                )

                            bitmap.recycle()
                            hardwareBuffer.close()

                            if (outputBitmap == null) {

                                writeResult(
                                    false,
                                    "screenshot failed: bitmap copy is null"
                                )

                                return
                            }

                            val file =
                                File(
                                    "/sdcard/agent_screenshot.png"
                                )

                            FileOutputStream(file).use { stream ->

                                outputBitmap.compress(
                                    Bitmap.CompressFormat.PNG,
                                    100,
                                    stream
                                )
                            }

                            outputBitmap.recycle()

                            val size =
                                file.length()

                            if (
                                file.exists() &&
                                size > 0
                            ) {

                                writeResult(
                                    true,
                                    "screenshot verified: ${file.absolutePath} ($size bytes)"
                                )

                            } else {

                                writeResult(
                                    false,
                                    "screenshot failed: output file is empty"
                                )
                            }

                        } catch (e: Exception) {

                            writeResult(
                                false,
                                "screenshot processing failed: ${e.message}"
                            )
                        }
                    }

                    override fun onFailure(
                        errorCode: Int
                    ) {

                        writeResult(
                            false,
                            "screenshot failed: errorCode=$errorCode"
                        )
                    }
                }
            )

        } catch (e: Exception) {

            writeResult(
                false,
                "screenshot request failed: ${e.message}"
            )
        }
    }

    private fun executeTap(cmd: JSONObject) {

        val beforeRoot = getTargetRoot()

        val beforePackage =
            beforeRoot?.packageName?.toString() ?: ""

        val beforeSignature =
            beforeRoot?.let {
                uiSignature(it)
            } ?: ""

        beforeRoot?.recycle()

        val elementId =
            cmd.optInt("element_id", -1)

        var node: AccessibilityNodeInfo? = null

        var actionStarted = false

        if (elementId >= 0) {

            node = findElementForTap(elementId)

            if (
                node != null &&
                node.isEnabled &&
                node.isClickable
            ) {
                actionStarted =
                    node.performAction(
                        AccessibilityNodeInfo.ACTION_CLICK
                    )
            }

            if (!actionStarted && node != null) {

                val parent =
                    findClickableParent(node)

                if (
                    parent != null &&
                    parent.isEnabled
                ) {
                    actionStarted =
                        parent.performAction(
                            AccessibilityNodeInfo.ACTION_CLICK
                        )
                }

                parent?.recycle()
            }
        }

        node?.recycle()

        /*
         * IMPORTANT:
         *
         * If AccessibilityNodeInfo lookup/action failed,
         * use the bounds stored in ui_state.json.
         *
         * This makes:
         *
         * {"action":"tap","element_id":16}
         *
         * work even if the live accessibility node cannot
         * be matched directly.
         */
        if (!actionStarted && elementId >= 0) {

            val center =
                getElementCenterFromUiState(elementId)

            if (center != null) {
                actionStarted =
                    tap(center.first, center.second)
            }
        }

        /*
         * Explicit coordinate fallback.
         */
        if (!actionStarted) {

            val x =
                cmd.optDouble("x", -1.0)
                    .toFloat()

            val y =
                cmd.optDouble("y", -1.0)
                    .toFloat()

            if (x >= 0 && y >= 0) {
                actionStarted =
                    tap(x, y)
            }
        }

        if (!actionStarted) {

            writeResult(
                false,
                "tap execution failed"
            )

            return
        }

        handler.postDelayed(
            {
                verifyTap(
                    beforePackage,
                    beforeSignature
                )
            },
            700
        )
    }

    private fun getElementCenterFromUiState(
        elementId: Int
    ): Pair<Float, Float>? {

        return try {

            if (!uiFile.exists()) {
                return null
            }

            val state =
                JSONObject(
                    uiFile.readText(
                        Charset.forName("UTF-8")
                    )
                )

            val elements =
                state.optJSONArray("elements")
                    ?: return null

            if (
                elementId < 0 ||
                elementId >= elements.length()
            ) {
                return null
            }

            val element =
                elements.getJSONObject(elementId)

            val bounds =
                element.optJSONArray("bounds")
                    ?: return null

            if (bounds.length() < 4) {
                return null
            }

            val left =
                bounds.getInt(0).toFloat()

            val top =
                bounds.getInt(1).toFloat()

            val right =
                bounds.getInt(2).toFloat()

            val bottom =
                bounds.getInt(3).toFloat()

            val centerX =
                (left + right) / 2f

            val centerY =
                (top + bottom) / 2f

            if (
                right <= left ||
                bottom <= top
            ) {
                return null
            }

            Pair(centerX, centerY)

        } catch (_: Exception) {
            null
        }
    }

    private fun findElementForTap(
        elementId: Int
    ): AccessibilityNodeInfo? {

        try {

            if (!uiFile.exists()) {
                return findByIndex(elementId)
            }

            val state =
                JSONObject(
                    uiFile.readText(
                        Charset.forName("UTF-8")
                    )
                )

            val elements =
                state.optJSONArray("elements")
                    ?: return findByIndex(elementId)

            if (
                elementId < 0 ||
                elementId >= elements.length()
            ) {
                return null
            }

            val target =
                elements.getJSONObject(elementId)

            val targetText =
                target.optString("text", "")

            val targetDesc =
                target.optString(
                    "content_desc",
                    ""
                )

            val targetResource =
                target.optString(
                    "resource_id",
                    ""
                )

            val targetClass =
                target.optString(
                    "class",
                    ""
                )

            val targetPkg =
                target.optString(
                    "package",
                    ""
                )

            val root =
                getTargetRoot()
                    ?: return null

            val currentPackage =
                root.packageName?.toString() ?: ""

            if (
                targetPkg.isNotEmpty() &&
                currentPackage != targetPkg
            ) {
                root.recycle()
                return null
            }

            if (targetResource.isNotEmpty()) {

                val found =
                    findMatchingNode(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.RESOURCE
                    )

                if (found != null) {
                    root.recycle()
                    return found
                }
            }

            if (targetDesc.isNotEmpty()) {

                val found =
                    findMatchingNode(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.CONTENT_DESC
                    )

                if (found != null) {
                    root.recycle()
                    return found
                }
            }

            if (targetText.isNotEmpty()) {

                val found =
                    findMatchingNode(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.TEXT
                    )

                if (found != null) {
                    root.recycle()
                    return found
                }
            }

            val found =
                findByIndexFromRoot(
                    root,
                    elementId
                )

            root.recycle()

            return found

        } catch (_: Exception) {

            return findByIndex(elementId)
        }
    }

    private enum class MatchMode {
        RESOURCE,
        CONTENT_DESC,
        TEXT
    }

    private fun findMatchingNode(
        node: AccessibilityNodeInfo,
        resourceId: String,
        text: String,
        contentDesc: String,
        className: String,
        mode: MatchMode
    ): AccessibilityNodeInfo? {

        val nodeResource =
            node.viewIdResourceName ?: ""

        val nodeText =
            node.text?.toString() ?: ""

        val nodeDesc =
            node.contentDescription?.toString()
                ?: ""

        val nodeClass =
            node.className?.toString() ?: ""

        val matches =
            when (mode) {

                MatchMode.RESOURCE ->
                    nodeResource == resourceId &&
                        classNameMatches(
                            nodeClass,
                            className
                        )

                MatchMode.CONTENT_DESC ->
                    nodeDesc == contentDesc &&
                        classNameMatches(
                            nodeClass,
                            className
                        )

                MatchMode.TEXT ->
                    nodeText == text &&
                        classNameMatches(
                            nodeClass,
                            className
                        )
            }

        if (matches && node.isEnabled) {
            return AccessibilityNodeInfo.obtain(node)
        }

        for (i in 0 until node.childCount) {

            val child =
                node.getChild(i)
                    ?: continue

            val found =
                findMatchingNode(
                    child,
                    resourceId,
                    text,
                    contentDesc,
                    className,
                    mode
                )

            child.recycle()

            if (found != null) {
                return found
            }
        }

        return null
    }

    private fun classNameMatches(
        current: String,
        expected: String
    ): Boolean {

        if (expected.isEmpty()) {
            return true
        }

        return current == expected
    }

    private fun findClickableParent(
        node: AccessibilityNodeInfo
    ): AccessibilityNodeInfo? {

        var parent =
            node.parent

        while (parent != null) {

            if (
                parent.isClickable &&
                parent.isEnabled
            ) {
                return parent
            }

            val next =
                parent.parent

            parent.recycle()

            parent = next
        }

        return null
    }

    private fun getTargetRoot(): AccessibilityNodeInfo? {
        val lockedPackage = targetPackage

        // استخدم الجذر النشط مباشرة إذا كان من التطبيق المستهدف.
        // هذا هو نفس المسار الذي ينجح معه dumpUi().
        val activeRoot = rootInActiveWindow

        if (activeRoot != null) {
            val activePackage =
                activeRoot.packageName?.toString() ?: ""

            if (
                lockedPackage.isEmpty() ||
                activePackage == lockedPackage
            ) {
                return activeRoot
            }

            activeRoot.recycle()
        }

        // fallback: البحث في النوافذ إذا لم يكن rootInActiveWindow مناسبًا.
        if (lockedPackage.isNotEmpty()) {
            var activeFocused: AccessibilityNodeInfo? = null
            var active: AccessibilityNodeInfo? = null
            var focused: AccessibilityNodeInfo? = null
            var anyMatch: AccessibilityNodeInfo? = null

            for (window in windows) {
                try {
                    val root = window.root ?: continue
                    val pkg = root.packageName?.toString() ?: ""

                    if (pkg != lockedPackage) {
                        root.recycle()
                        continue
                    }

                    if (anyMatch == null) {
                        anyMatch = AccessibilityNodeInfo.obtain(root)
                    }

                    if (window.isActive && window.isFocused) {
                        activeFocused =
                            AccessibilityNodeInfo.obtain(root)
                        root.recycle()
                        break
                    }

                    if (window.isActive && active == null) {
                        active = AccessibilityNodeInfo.obtain(root)
                    }

                    if (window.isFocused && focused == null) {
                        focused = AccessibilityNodeInfo.obtain(root)
                    }

                    root.recycle()
                } catch (_: Exception) {
                }
            }

            activeFocused?.let {
                active?.recycle()
                focused?.recycle()
                anyMatch?.recycle()
                return it
            }

            active?.let {
                focused?.recycle()
                anyMatch?.recycle()
                return it
            }

            focused?.let {
                anyMatch?.recycle()
                return it
            }

            return anyMatch
        }

        return rootInActiveWindow
    }

    private fun executeType(cmd: JSONObject) {
        val text = cmd.optString("text", "")

        var node: AccessibilityNodeInfo? = null

        // ابحث مباشرة عن حقل الإدخال المركز حاليًا
        val root = getTargetRoot()

        if (root != null) {
            try {
                node = findFocusedEditable(root)
            } catch (_: Exception) {
            }

            root.recycle()
        }

        // محاولة ثانية بعد تحديث واجهة المستخدم
        if (node == null) {
            try {
                dumpUi()
            } catch (_: Exception) {
            }

            SystemClock.sleep(150)

            val retryRoot = getTargetRoot()

            if (retryRoot != null) {
                try {
                    node = findFocusedEditable(retryRoot)
                } catch (_: Exception) {
                }

                retryRoot.recycle()
            }
        }

        if (node == null) {
            writeResult(
                false,
                "type execution failed: focused editable element not found"
            )
            return
        }

        val editable =
            node.isEditable ||
            node.className?.toString()
                ?.contains("EditText", ignoreCase = true) == true

        if (!editable) {
            node.recycle()

            writeResult(
                false,
                "type execution failed: target is not editable"
            )
            return
        }

        val actionStarted = try {
            node.performAction(
                AccessibilityNodeInfo.ACTION_SET_TEXT,
                Bundle().apply {
                    putCharSequence(
                        AccessibilityNodeInfo
                            .ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                        text
                    )
                }
            )
        } catch (_: Exception) {
            false
        }

        node.recycle()
        node = null

        if (!actionStarted) {
            writeResult(
                false,
                "type execution failed: ACTION_SET_TEXT rejected"
            )
            return
        }

        handler.postDelayed({
            verifyType(text)
        }, 700)
    }

    private fun findFocusedEditable(
        node: AccessibilityNodeInfo
    ): AccessibilityNodeInfo? {

        val isEditable =
            node.isEditable ||
            node.className?.toString()
                ?.contains("EditText", ignoreCase = true) == true

        if (
            node.isFocused &&
            node.isEnabled &&
            isEditable
        ) {
            return AccessibilityNodeInfo.obtain(node)
        }

        for (i in 0 until node.childCount) {

            val child =
                node.getChild(i)
                    ?: continue

            val found =
                findFocusedEditable(child)

            child.recycle()

            if (found != null) {
                return found
            }
        }

        return null
    }

    private fun findElementForType(
        elementId: Int
    ): AccessibilityNodeInfo? {

        if (!uiFile.exists()) {
            return findEditableByIndex(elementId)
        }

        return try {
            val state = JSONObject(
                uiFile.readText(Charset.forName("UTF-8"))
            )

            val elements = state.optJSONArray("elements")
                ?: return findEditableByIndex(elementId)

            if (elementId < 0 || elementId >= elements.length()) {
                return null
            }

            val target = elements.getJSONObject(elementId)

            val targetText = target.optString("text", "")
            val targetDesc = target.optString("content_desc", "")
            val targetResource = target.optString("resource_id", "")
            val targetClass = target.optString("class", "")
            val targetPackage = target.optString("package", "")

            val root = getTargetRoot() ?: return null

            try {
                val currentPackage =
                    root.packageName?.toString() ?: ""

                if (
                    targetPackage.isNotEmpty() &&
                    currentPackage != targetPackage
                ) {
                    return null
                }

                // 1. resource_id
                if (targetResource.isNotEmpty()) {
                    val found = findEditableMatching(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.RESOURCE
                    )

                    if (found != null) {
                        return found
                    }
                }

                // 2. content description
                if (targetDesc.isNotEmpty()) {
                    val found = findEditableMatching(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.CONTENT_DESC
                    )

                    if (found != null) {
                        return found
                    }
                }

                // 3. text
                if (targetText.isNotEmpty()) {
                    val found = findEditableMatching(
                        root,
                        targetResource,
                        targetText,
                        targetDesc,
                        targetClass,
                        MatchMode.TEXT
                    )

                    if (found != null) {
                        return found
                    }
                }

                // 4. fallback إلى العنصر الحالي بنفس index
                return findEditableByIndexFromRoot(root, elementId)

            } finally {
                root.recycle()
            }

        } catch (_: Exception) {
            return findEditableByIndex(elementId)
        }
    }

    private fun findEditableMatching(
        node: AccessibilityNodeInfo,
        resourceId: String,
        text: String,
        contentDesc: String,
        className: String,
        mode: MatchMode
    ): AccessibilityNodeInfo? {

        val nodeResource = node.viewIdResourceName ?: ""
        val nodeText = node.text?.toString() ?: ""
        val nodeDesc = node.contentDescription?.toString() ?: ""
        val nodeClass = node.className?.toString() ?: ""

        val matches = when (mode) {
            MatchMode.RESOURCE ->
                nodeResource == resourceId &&
                classNameMatches(nodeClass, className)

            MatchMode.CONTENT_DESC ->
                nodeDesc == contentDesc &&
                classNameMatches(nodeClass, className)

            MatchMode.TEXT ->
                nodeText == text &&
                classNameMatches(nodeClass, className)
        }

        val editable =
            node.isEditable ||
            nodeClass.contains("EditText", ignoreCase = true)

        if (matches && node.isEnabled && editable) {
            return AccessibilityNodeInfo.obtain(node)
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue

            val found = findEditableMatching(
                child,
                resourceId,
                text,
                contentDesc,
                className,
                mode
            )

            child.recycle()

            if (found != null) {
                return found
            }
        }

        return null
    }

    private fun findEditableByIndex(
        target: Int
    ): AccessibilityNodeInfo? {

        val root = getTargetRoot() ?: return null

        val found = findEditableByIndexFromRoot(
            root,
            target
        )

        root.recycle()

        return found
    }

    private fun findEditableByIndexFromRoot(
        root: AccessibilityNodeInfo,
        target: Int
    ): AccessibilityNodeInfo? {

        val counter = intArrayOf(0)

        return findEditableRecursive(
            root,
            target,
            counter
        )
    }

    private fun findEditableRecursive(
        node: AccessibilityNodeInfo,
        target: Int,
        counter: IntArray
    ): AccessibilityNodeInfo? {

        val currentId = counter[0]

        val editable =
            node.isEditable ||
            node.className?.toString()
                ?.contains("EditText", ignoreCase = true) == true

        if (currentId == target && editable) {
            return AccessibilityNodeInfo.obtain(node)
        }

        counter[0]++

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue

            val found = findEditableRecursive(
                child,
                target,
                counter
            )

            child.recycle()

            if (found != null) {
                return found
            }
        }

        return null
    }

    private fun executeSwipe(
        cmd: JSONObject
    ) {

        val beforeRoot =
            rootInActiveWindow

        val beforePackage =
            beforeRoot?.packageName?.toString()
                ?: ""

        val beforeSignature =
            beforeRoot?.let {
                uiSignature(it)
            } ?: ""

        beforeRoot?.recycle()

        val actionStarted =
            swipe(
                cmd.optDouble("x1").toFloat(),
                cmd.optDouble("y1").toFloat(),
                cmd.optDouble("x2").toFloat(),
                cmd.optDouble("y2").toFloat(),
                cmd.optLong(
                    "duration_ms",
                    400
                )
            )

        if (!actionStarted) {

            writeResult(
                false,
                "swipe execution failed"
            )

        } else {

            handler.postDelayed(
                {
                    verifySwipe(
                        beforePackage,
                        beforeSignature
                    )
                },
                700
            )
        }
    }

    private fun executeScroll(cmd: JSONObject) {

        val beforeRoot = getTargetRoot()

        val beforePackage =
            beforeRoot?.packageName?.toString() ?: ""

        val beforeSignature =
            beforeRoot?.let { uiSignature(it) } ?: ""

        beforeRoot?.recycle()

        val elementId = cmd.optInt("element_id", -1)
        val direction = cmd.optString("direction", "down")

        var actionStarted = false

        if (elementId >= 0) {

            val node = findElementForTap(elementId)

            if (node != null && node.isScrollable) {

                actionStarted = node.performAction(
                    if (direction == "up" || direction == "left") {
                        AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                    } else {
                        AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                    }
                )
            }

            node?.recycle()
        }

        if (!actionStarted) {

            // fallback: إيماءة سحب عامة عبر منطقة الشاشة
            val metrics = resources.displayMetrics
            val width = metrics.widthPixels.toFloat()
            val height = metrics.heightPixels.toFloat()

            val coords = when (direction) {
                "up" -> listOf(width / 2f, height * 0.3f, width / 2f, height * 0.7f)
                "down" -> listOf(width / 2f, height * 0.7f, width / 2f, height * 0.3f)
                "left" -> listOf(width * 0.3f, height / 2f, width * 0.7f, height / 2f)
                "right" -> listOf(width * 0.7f, height / 2f, width * 0.3f, height / 2f)
                else -> listOf(width / 2f, height * 0.7f, width / 2f, height * 0.3f)
            }

            val (x1, y1, x2, y2) = coords

            actionStarted = swipe(x1, y1, x2, y2, 400)
        }

        if (!actionStarted) {

            writeResult(
                false,
                "scroll execution failed"
            )

            return
        }

        handler.postDelayed(
            {
                verifyScroll(
                    beforePackage,
                    beforeSignature
                )
            },
            500
        )
    }

    private fun verifyScroll(
        beforePackage: String,
        beforeSignature: String
    ) {
        try {
            val root =
                getTargetRoot()
                    ?: rootInActiveWindow

            if (root == null) {
                writeResult(
                    false,
                    "scroll verification failed: no active root"
                )
                return
            }

            val afterPackage =
                root.packageName?.toString() ?: ""

            val afterSignature =
                uiSignature(root)

            val changed =
                afterPackage != beforePackage ||
                    afterSignature != beforeSignature

            if (changed) {
                writeResult(
                    true,
                    "scroll verified"
                )
            } else {
                writeResult(
                    false,
                    "scroll not verified: UI did not change"
                )
            }

            root.recycle()

        } catch (e: Exception) {
            writeResult(
                false,
                "scroll verification failed: ${e.message}"
            )
        }
    }

    private fun executeLongPress(cmd: JSONObject) {

        val beforeRoot = getTargetRoot()

        val beforePackage =
            beforeRoot?.packageName?.toString() ?: ""

        val beforeSignature =
            beforeRoot?.let { uiSignature(it) } ?: ""

        beforeRoot?.recycle()

        val elementId = cmd.optInt("element_id", -1)

        var node: AccessibilityNodeInfo? = null
        var actionStarted = false

        if (elementId >= 0) {

            node = findElementForTap(elementId)

            if (
                node != null &&
                node.isEnabled &&
                node.isLongClickable
            ) {
                actionStarted = node.performAction(
                    AccessibilityNodeInfo.ACTION_LONG_CLICK
                )
            }
        }

        node?.recycle()

        if (!actionStarted && elementId >= 0) {

            // إعادة قراءة العنصر من شجرة Accessibility الحديثة
            // قبل تنفيذ الإيماءة، بدل الاعتماد على bounds قديمة.
            val freshNode = findElementForTap(elementId)

            if (freshNode != null) {

                val rect = Rect()

                freshNode.getBoundsInScreen(rect)

                if (
                    !rect.isEmpty &&
                    rect.width() > 0 &&
                    rect.height() > 0
                ) {
                    val centerX = rect.centerX().toFloat()
                    val centerY = rect.centerY().toFloat()

                    actionStarted =
                        longPress(
                            centerX,
                            centerY
                        )
                }

                freshNode.recycle()
            }
        }

        if (!actionStarted) {

            val x = cmd.optDouble("x", -1.0).toFloat()
            val y = cmd.optDouble("y", -1.0).toFloat()

            if (x >= 0 && y >= 0) {
                actionStarted = longPress(x, y)
            }
        }

        if (!actionStarted) {

            writeResult(
                false,
                "long_press execution failed"
            )

            return
        }

        handler.postDelayed(
            {
                verifyLongPress(
                    beforePackage,
                    beforeSignature
                )
            },
            700
        )
    }

    private fun verifyLongPress(
        beforePackage: String,
        beforeSignature: String
    ) {
        try {
            val root =
                getTargetRoot()
                    ?: rootInActiveWindow

            if (root == null) {
                writeResult(
                    false,
                    "long_press verification failed: no active root"
                )
                return
            }

            val afterPackage =
                root.packageName?.toString() ?: ""

            val afterSignature =
                uiSignature(root)

            val changed =
                afterPackage != beforePackage ||
                    afterSignature != beforeSignature

            if (changed) {
                writeResult(
                    true,
                    "long_press verified"
                )
            } else {
                writeResult(
                    false,
                    "long_press not verified: UI did not change"
                )
            }

            root.recycle()

        } catch (e: Exception) {
            writeResult(
                false,
                "long_press verification failed: ${e.message}"
            )
        }
    }

    private fun longPress(x: Float, y: Float): Boolean {

        if (x < 0 || y < 0) {
            return false
        }

        val path = Path().apply { moveTo(x, y) }

        return dispatchGesture(
            GestureDescription.Builder()
                .addStroke(
                    GestureDescription.StrokeDescription(path, 0, 1000)
                )
                .build(),
            null,
            null
        )
    }

    private fun executeListApps() {

        try {

            val intent = Intent(
                Intent.ACTION_MAIN
            ).apply {
                addCategory(
                    Intent.CATEGORY_LAUNCHER
                )
            }

            val activities =
                packageManager.queryIntentActivities(
                    intent,
                    0
                )

            val apps = JSONArray()

            val seenPackages =
                mutableSetOf<String>()

            for (resolveInfo in activities) {

                val packageName =
                    resolveInfo.activityInfo?.packageName
                        ?: continue

                if (packageName.isEmpty()) {
                    continue
                }

                if (!seenPackages.add(packageName)) {
                    continue
                }

                val label =
                    resolveInfo.loadLabel(
                        packageManager
                    )?.toString() ?: packageName

                apps.put(
                    JSONObject()
                        .put("name", label)
                        .put("package", packageName)
                )
            }

            val data =
                JSONObject()
                    .put("apps", apps)
                    .put("count", apps.length())

            writeResult(
                true,
                "apps discovered",
                data
            )

        } catch (e: Exception) {

            writeResult(
                false,
                "list_apps failed: ${e.message}"
            )
        }
    }

    private fun executeOpenApp(
        cmd: JSONObject
    ) {

        val packageName =
            cmd.optString(
                "package",
                ""
            ).trim()

        if (packageName.isEmpty()) {

            writeResult(
                false,
                "missing package"
            )

            return
        }

        try {

            targetPackage =
                packageName

            val intent =
                packageManager
                    .getLaunchIntentForPackage(
                        packageName
                    )

            if (intent == null) {

                targetPackage = ""

                writeResult(
                    false,
                    "no launcher activity: $packageName"
                )

                return
            }

            intent.addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
            )

            startActivity(intent)

            handler.postDelayed(
                {

                    try {

                        val root =
                            rootInActiveWindow

                        val currentPackage =
                            root?.packageName?.toString()
                                ?: ""

                        if (
                            currentPackage ==
                            packageName
                        ) {

                            if (root != null) {
                                writeUiRoot(root)
                            }

                            writeResult(
                                true,
                                "open_app verified: $packageName"
                            )

                        } else {

                            writeResult(
                                false,
                                "open_app not verified: expected=$packageName current=$currentPackage"
                            )

                            targetPackage = ""
                        }

                        root?.recycle()

                    } catch (e: Exception) {

                        targetPackage = ""

                        writeResult(
                            false,
                            "open_app verification failed: ${e.message}"
                        )
                    }

                },
                1000
            )

        } catch (e: Exception) {

            targetPackage = ""

            writeResult(
                false,
                "open_app failed: ${e.message}"
            )
        }
    }

    private fun verifyTap(
        beforePackage: String,
        beforeSignature: String
    ) {

        try {

            // محاولة أولى عبر getTargetRoot، وإذا فشلت (مثلاً بسبب
            // تحوّل مؤقت في قائمة النوافذ عند ظهور لوحة المفاتيح)
            // نرجع إلى rootInActiveWindow كخيار أخير بدل الفشل الفوري.
            val root =
                getTargetRoot()
                    ?: rootInActiveWindow

            if (root == null) {

                writeResult(
                    false,
                    "tap verification failed: no active root"
                )

                return
            }

            val afterPackage =
                root.packageName?.toString()
                    ?: ""

            val afterSignature =
                uiSignature(root)

            val changed =
                afterPackage != beforePackage ||
                    afterSignature != beforeSignature

            if (changed) {

                writeResult(
                    true,
                    "tap verified"
                )

                root.recycle()

                return
            }

            // التحقق الدلالي: حتى لو لم تتغيّر شجرة الواجهة بشكل ملحوظ،
            // اعتبر الضغط ناجحًا إذا أصبح هناك عنصر قابل للتحرير مُركَّز
            // (مثال: الضغط على حقل بحث لا يغيّر الشجرة كثيرًا لكنه يفتح لوحة المفاتيح).
            val focusedEditable =
                try {
                    findFocusedEditable(root)
                } catch (_: Exception) {
                    null
                }

            if (focusedEditable != null) {

                focusedEditable.recycle()

                writeResult(
                    true,
                    "tap verified: focused editable found"
                )

                root.recycle()

                return
            }

            writeResult(
                false,
                "tap not verified: UI did not change"
            )

            root.recycle()

        } catch (e: Exception) {

            writeResult(
                false,
                "tap verification failed: ${e.message}"
            )
        }
    }

    private fun verifyType(
        expectedText: String
    ) {

        try {

            val root =
                rootInActiveWindow

            if (root == null) {

                writeResult(
                    false,
                    "type verification failed: no active root"
                )

                return
            }

            val found =
                containsText(
                    root,
                    expectedText
                )

            if (found) {

                writeResult(
                    true,
                    "type verified"
                )

            } else {

                writeResult(
                    false,
                    "type not verified: text not found"
                )
            }

            root.recycle()

        } catch (e: Exception) {

            writeResult(
                false,
                "type verification failed: ${e.message}"
            )
        }
    }

    private fun verifySwipe(
        beforePackage: String,
        beforeSignature: String
    ) {

        try {

            val root =
                rootInActiveWindow

            if (root == null) {

                writeResult(
                    false,
                    "swipe verification failed: no active root"
                )

                return
            }

            val afterPackage =
                root.packageName?.toString()
                    ?: ""

            val afterSignature =
                uiSignature(root)

            val changed =
                afterPackage != beforePackage ||
                    afterSignature != beforeSignature

            if (changed) {

                writeResult(
                    true,
                    "swipe verified"
                )

            } else {

                writeResult(
                    false,
                    "swipe not verified: UI did not change"
                )
            }

            root.recycle()

        } catch (e: Exception) {

            writeResult(
                false,
                "swipe verification failed: ${e.message}"
            )
        }
    }

    private fun containsText(
        node: AccessibilityNodeInfo,
        expectedText: String
    ): Boolean {

        val nodeText =
            node.text?.toString() ?: ""

        if (nodeText == expectedText) {
            return true
        }

        for (i in 0 until node.childCount) {

            val child =
                node.getChild(i)
                    ?: continue

            val found =
                containsText(
                    child,
                    expectedText
                )

            child.recycle()

            if (found) {
                return true
            }
        }

        return false
    }

    private fun uiSignature(
        root: AccessibilityNodeInfo
    ): String {

        return try {

            val nodes =
                JSONArray()

            walk(
                root,
                nodes
            )

            nodes.toString()

        } catch (_: Exception) {
            ""
        }
    }

    private fun findByIndex(
        target: Int
    ): AccessibilityNodeInfo? {

        val root =
            rootInActiveWindow
                ?: return null

        val found =
            findByIndexFromRoot(
                root,
                target
            )

        root.recycle()

        return found
    }

    private fun findByIndexFromRoot(
        root: AccessibilityNodeInfo,
        target: Int
    ): AccessibilityNodeInfo? {

        val counter =
            intArrayOf(0)

        return findRecursive(
            root,
            target,
            counter
        )
    }

    private fun findRecursive(
        node: AccessibilityNodeInfo,
        target: Int,
        counter: IntArray
    ): AccessibilityNodeInfo? {

        if (counter[0] == target) {
            return AccessibilityNodeInfo.obtain(node)
        }

        counter[0]++

        for (i in 0 until node.childCount) {

            val child =
                node.getChild(i)
                    ?: continue

            val found =
                findRecursive(
                    child,
                    target,
                    counter
                )

            child.recycle()

            if (found != null) {
                return found
            }
        }

        return null
    }

    private fun tap(
        x: Float,
        y: Float
    ): Boolean {

        if (x < 0 || y < 0) {
            return false
        }

        val path =
            Path().apply {
                moveTo(x, y)
            }

        return dispatchGesture(
            GestureDescription.Builder()
                .addStroke(
                    GestureDescription.StrokeDescription(
                        path,
                        0,
                        60
                    )
                )
                .build(),
            null,
            null
        )
    }

    private fun swipe(
        x1: Float,
        y1: Float,
        x2: Float,
        y2: Float,
        duration: Long
    ): Boolean {

        val path =
            Path().apply {
                moveTo(x1, y1)
                lineTo(x2, y2)
            }

        return dispatchGesture(
            GestureDescription.Builder()
                .addStroke(
                    GestureDescription.StrokeDescription(
                        path,
                        0,
                        duration.coerceIn(
                            50,
                            5000
                        )
                    )
                )
                .build(),
            null,
            null
        )
    }

    private fun writeResult(
        ok: Boolean,
        msg: String,
        data: JSONObject? = null
    ) {

        try {

            val result = JSONObject()
                .put("ok", ok)
                .put("message", msg)
                .put("command_id", currentCommandId)
                .put(
                    "timestamp",
                    System.currentTimeMillis()
                )

            if (data != null) {
                result.put("data", data)
            }

            resultFile.writeText(
                result.toString(2),
                Charset.forName("UTF-8")
            )

        } catch (_: Exception) {}
    }
}
