package com.umrahconnect.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.os.Bundle;
import android.webkit.*;
import android.widget.Toast;
import androidx.activity.OnBackPressedCallback;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;
import com.umrahconnect.app.databinding.ActivityMainBinding;

public class MainActivity extends AppCompatActivity {

    private ActivityMainBinding binding;
    private WebView webView;
    private SwipeRefreshLayout swipeRefresh;

    // File chooser for profile picture upload
    private ValueCallback<Uri[]> filePathCallback;
    private static final int FILE_CHOOSER_REQUEST = 1001;

    // The server URL injected at build time via BuildConfig
    private static final String SERVER_URL = BuildConfig.SERVER_URL;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        binding = ActivityMainBinding.inflate(getLayoutInflater());
        setContentView(binding.getRoot());

        webView = binding.webView;
        swipeRefresh = binding.swipeRefresh;

        // ── Edge-to-edge (enforced on targetSdk 35): keep content out from
        // under the status/navigation bars by padding the root view. ────────
        ViewCompat.setOnApplyWindowInsetsListener(binding.getRoot(), (v, windowInsets) -> {
            Insets bars = windowInsets.getInsets(
                    WindowInsetsCompat.Type.systemBars()
                            | WindowInsetsCompat.Type.displayCutout()
                            | WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });

        // ── Back gesture (predictive-back friendly): go back in WebView
        // history when possible, otherwise let the system handle it. ────────
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        // ── WebView settings ──────────────────────────────────────────────
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);      // needed for localStorage (JWT token)
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        // Debug builds talk HTTP to the local dev server; release builds are HTTPS-only.
        settings.setMixedContentMode(BuildConfig.DEBUG
                ? WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                : WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        // Set user agent so the server knows it's the Android app
        settings.setUserAgentString(settings.getUserAgentString()
                + " UmrahConnectApp/" + BuildConfig.VERSION_NAME);

        // ── WebViewClient: keep navigation inside the app ─────────────────
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                // Let our server URLs load in-app
                if (url.startsWith(SERVER_URL)
                        || (BuildConfig.DEBUG && url.startsWith("http://10.0.2.2"))) {
                    return false;
                }
                // Open external URLs (WhatsApp, Facebook, etc.) in the browser
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                startActivity(intent);
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                swipeRefresh.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request.isForMainFrame()) {
                    swipeRefresh.setRefreshing(false);
                    // Show offline page when server unreachable.
                    // loadDataWithBaseURL handles UTF-8 (Arabic) correctly where loadData does not.
                    if (!isNetworkAvailable()) {
                        view.loadDataWithBaseURL(null, offlinePage(), "text/html", "UTF-8", null);
                    }
                }
            }
        });

        // ── ChromeClient: file chooser + console logs ─────────────────────
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView webView,
                                             ValueCallback<Uri[]> filePathCallback2,
                                             FileChooserParams fileChooserParams) {
                filePathCallback = filePathCallback2;
                Intent intent = fileChooserParams.createIntent();
                try {
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                } catch (Exception e) {
                    filePathCallback = null;
                    Toast.makeText(MainActivity.this,
                            "Cannot open file chooser", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage msg) {
                // Forward JS console messages to Android logcat (debug builds only)
                if (BuildConfig.DEBUG) {
                    android.util.Log.d("UmrahConnectJS",
                            msg.sourceId() + ":" + msg.lineNumber() + " " + msg.message());
                }
                return true;
            }
        });

        // ── Swipe-to-refresh ─────────────────────────────────────────────
        swipeRefresh.setColorSchemeColors(
                getColor(R.color.primary), getColor(R.color.secondary));
        swipeRefresh.setOnRefreshListener(() -> webView.reload());

        // ── Restore or load ───────────────────────────────────────────────
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(SERVER_URL + buildInitialPath(getIntent()));
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String path = buildInitialPath(intent);
        if (!path.equals("/")) {
            webView.loadUrl(SERVER_URL + path);
        }
    }

    /**
     * Translate an incoming deep-link URI into a path (incl. query + fragment) on our server.
     * Examples:
     *   umrahconnect://announcement/42            -> /announcement/42
     *   umrahconnect://dashboard                  -> /dashboard
     *   umrahconnect://my-requests?chat=5         -> /my-requests?chat=5
     *   umrahconnect://dashboard?chat=5&ann=3     -> /dashboard?chat=5&ann=3
     * The query string MUST be preserved — chat deep-links (?chat=&ann=) rely on it to
     * auto-open the right conversation. Defaults to "/" for normal launches.
     */
    private String buildInitialPath(Intent intent) {
        if (intent == null || intent.getData() == null) return "/";
        Uri uri = intent.getData();
        String host = uri.getHost();
        String path = uri.getPath();

        StringBuilder sb = new StringBuilder();
        boolean hasHost = host != null && !host.isEmpty();
        boolean hasPath = path != null && !path.isEmpty();
        if (hasHost) sb.append('/').append(host);   // e.g. host=announcement, path=/42 -> /announcement/42
        if (hasPath) sb.append(path);
        if (sb.length() == 0) return "/";

        // Preserve query (?chat=5&ann=3) and fragment so notification deep-links land correctly
        String query = uri.getEncodedQuery();
        if (query != null && !query.isEmpty()) sb.append('?').append(query);
        String fragment = uri.getEncodedFragment();
        if (fragment != null && !fragment.isEmpty()) sb.append('#').append(fragment);
        return sb.toString();
    }

    // ── Save/restore WebView state across rotations ───────────────────────
    @Override
    protected void onSaveInstanceState(@NonNull Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    // ── File chooser result ───────────────────────────────────────────────
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER_REQUEST) {
            if (filePathCallback == null) return;
            Uri[] results = null;
            if (resultCode == Activity.RESULT_OK && data != null) {
                String dataString = data.getDataString();
                if (dataString != null) {
                    results = new Uri[]{Uri.parse(dataString)};
                }
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        }
    }

    // ── Network check ─────────────────────────────────────────────────────
    private boolean isNetworkAvailable() {
        ConnectivityManager cm =
                (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        NetworkInfo ni = cm.getActiveNetworkInfo();
        return ni != null && ni.isConnected();
    }

    // ── Offline page (bilingual, mirrors the web app's EN/AR + RTL support) ──
    private String offlinePage() {
        boolean ar = "ar".equals(java.util.Locale.getDefault().getLanguage());
        String dir   = ar ? "rtl" : "ltr";
        String lang  = ar ? "ar" : "en";
        String title = ar ? "رفقة عمرة" : "Umrah Connect";
        String body  = ar ? "لا يوجد اتصال بالإنترنت.<br>يرجى التحقق من الشبكة وإعادة المحاولة."
                           : "No internet connection.<br>Please check your network and try again.";
        String retry = ar ? "إعادة المحاولة" : "Try Again";
        return "<!DOCTYPE html><html lang='" + lang + "' dir='" + dir + "'><head>" +
            "<meta charset='utf-8'>" +
            "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
            "<style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;" +
            "min-height:100vh;margin:0;background:#f8f6f0;color:#2c3e50;text-align:center;padding:2rem;}" +
            "h1{font-size:3rem;margin-bottom:1rem;}h2{color:#1a5f4a;}p{color:#6c757d;margin-bottom:1.5rem;}" +
            "button{background:#1a5f4a;color:white;border:none;padding:0.75rem 2rem;" +
            "border-radius:8px;font-size:1rem;cursor:pointer;}</style></head>" +
            "<body><div><h1>🕋</h1><h2>" + title + "</h2>" +
            "<p>" + body + "</p>" +
            "<button onclick='location.reload()'>" + retry + "</button></div></body></html>";
    }
}
