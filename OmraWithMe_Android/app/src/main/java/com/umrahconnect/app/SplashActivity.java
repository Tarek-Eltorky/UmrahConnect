package com.umrahconnect.app;

import android.content.Intent;
import android.os.Bundle;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.splashscreen.SplashScreen;

/**
 * Splash screen using the AndroidX SplashScreen compat API
 * (system-level splash on Android 12+, themed splash on older versions).
 * Forwards immediately to MainActivity once content view is set — the splash
 * remains visible until MainActivity's WebView is ready to draw.
 */
public class SplashActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Must be called BEFORE super.onCreate
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);

        Intent next = new Intent(this, MainActivity.class);
        next.addFlags(Intent.FLAG_ACTIVITY_NO_ANIMATION);
        // Forward any deep-link intent data to MainActivity
        if (getIntent() != null) {
            next.setData(getIntent().getData());
            next.putExtras(getIntent());
        }
        startActivity(next);
        finish();
    }
}
