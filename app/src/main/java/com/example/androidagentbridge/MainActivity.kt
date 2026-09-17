package com.example.androidagentbridge

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val title = TextView(this).apply { text = "Android Agent Bridge"; textSize = 24f; setPadding(32,40,32,24) }
        val info = TextView(this).apply { text = "فعّل خدمة Accessibility ثم اتركها تعمل.\\n\\nملفات التواصل:\\n/sdcard/ui_state.json\\n/sdcard/agent_command.json\\n/sdcard/agent_result.json"; textSize = 16f; setPadding(32,8,32,24) }
        val button = Button(this).apply { text = "فتح إعدادات Accessibility"; setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) } }
        setContentView(LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; addView(title); addView(info); addView(button) })
    }
}
