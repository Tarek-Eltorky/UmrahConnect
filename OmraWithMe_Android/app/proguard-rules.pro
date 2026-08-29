# Keep the app's Activity classes (referenced from the manifest; kept explicitly
# so stack traces stay readable and nothing manifest-referenced is stripped).
-keep public class com.umrahconnect.app.MainActivity { *; }
-keep public class com.umrahconnect.app.SplashActivity { *; }

# No @JavascriptInterface bridge exists in this app today. If one is ever added,
# keep it like this so R8 does not strip/rename the methods the web app calls:
# -keepclassmembers class com.umrahconnect.app.SomeBridge {
#     @android.webkit.JavascriptInterface <methods>;
# }

# Readable crash reports from Play (retrace with the generated mapping.txt)
-keepattributes SourceFile,LineNumberTable
