#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlwapi.h>

static HINSTANCE g_hInst = NULL;
HMODULE hReal = NULL;
FARPROC p[8];
FARPROC p_real[8];
static volatile LONG g_jp = -1; /* -1: 未選択, 0: CN, 1: JP */
static volatile LONG g_ui_hooks = 0;
static wchar_t g_gameDir[MAX_PATH];
static HHOOK g_cbtHook = NULL;
static volatile LONG g_in_translate = 0;

static const char *names[8] = {
    "SteamInternal_CreateInterface", "SteamAPI_GetHSteamPipe", "SteamAPI_GetHSteamUser",
    "SteamAPI_RunCallbacks", "SteamAPI_RegisterCallback", "SteamAPI_Shutdown",
    "SteamAPI_Init", "SteamAPI_UnregisterCallback",
};

typedef void* (__cdecl *PFN_SteamInternal_CreateInterface)(const char*, int*);
typedef int   (__cdecl *PFN_SteamAPI_GetHSteamPipe)(void);
typedef int   (__cdecl *PFN_SteamAPI_GetHSteamUser)(void);
typedef void  (__cdecl *PFN_SteamAPI_RunCallbacks)(void);
typedef void  (__cdecl *PFN_SteamAPI_RegisterCallback)(void*, int);
typedef void  (__cdecl *PFN_SteamAPI_Shutdown)(void);
typedef int   (__cdecl *PFN_SteamAPI_Init)(void);
typedef void  (__cdecl *PFN_SteamAPI_UnregisterCallback)(void*, int);

static void install_ui_hooks(void);

void* __cdecl wrap_SteamInternal_CreateInterface(const char* a, int* b){ return ((PFN_SteamInternal_CreateInterface)(void*)p_real[0])(a,b); }
int   __cdecl wrap_SteamAPI_GetHSteamPipe(void){ return ((PFN_SteamAPI_GetHSteamPipe)(void*)p_real[1])(); }
int   __cdecl wrap_SteamAPI_GetHSteamUser(void){ return ((PFN_SteamAPI_GetHSteamUser)(void*)p_real[2])(); }
void  __cdecl wrap_SteamAPI_RunCallbacks(void){ ((PFN_SteamAPI_RunCallbacks)(void*)p_real[3])(); }
void  __cdecl wrap_SteamAPI_RegisterCallback(void* a, int b){ ((PFN_SteamAPI_RegisterCallback)(void*)p_real[4])(a,b); }
void  __cdecl wrap_SteamAPI_Shutdown(void){
    if (g_cbtHook) { UnhookWindowsHookEx(g_cbtHook); g_cbtHook = NULL; }
    ((PFN_SteamAPI_Shutdown)(void*)p_real[5])();
}
int   __cdecl wrap_SteamAPI_Init(void){
    int r = ((PFN_SteamAPI_Init)(void*)p_real[6])();
    if (g_jp == 1 && InterlockedCompareExchange(&g_ui_hooks, 1, 0) == 0) install_ui_hooks();
    return r;
}
void  __cdecl wrap_SteamAPI_UnregisterCallback(void* a, int b){ ((PFN_SteamAPI_UnregisterCallback)(void*)p_real[7])(a,b); }

static void load_real(void) {
    if (hReal) return;
    wchar_t path[MAX_PATH];
    lstrcpyW(path, g_gameDir);
    lstrcatW(path, L"\\steam_api_original.dll");
    hReal = LoadLibraryW(path);
    if (!hReal) return;
    for (int i = 0; i < 8; i++) p_real[i] = GetProcAddress(hReal, names[i]);
    p[0]=(FARPROC)(void*)wrap_SteamInternal_CreateInterface;
    p[1]=(FARPROC)(void*)wrap_SteamAPI_GetHSteamPipe;
    p[2]=(FARPROC)(void*)wrap_SteamAPI_GetHSteamUser;
    p[3]=(FARPROC)(void*)wrap_SteamAPI_RunCallbacks;
    p[4]=(FARPROC)(void*)wrap_SteamAPI_RegisterCallback;
    p[5]=(FARPROC)(void*)wrap_SteamAPI_Shutdown;
    p[6]=(FARPROC)(void*)wrap_SteamAPI_Init;
    p[7]=(FARPROC)(void*)wrap_SteamAPI_UnregisterCallback;
}

typedef HANDLE (WINAPI *PFN_CreateFileW)(LPCWSTR,DWORD,DWORD,LPSECURITY_ATTRIBUTES,DWORD,DWORD,HANDLE);
typedef HANDLE (WINAPI *PFN_CreateFileA)(LPCSTR,DWORD,DWORD,LPSECURITY_ATTRIBUTES,DWORD,DWORD,HANDLE);
typedef DWORD  (WINAPI *PFN_GetFileAttributesW)(LPCWSTR);
typedef DWORD  (WINAPI *PFN_GetFileAttributesA)(LPCSTR);
typedef BOOL   (WINAPI *PFN_GetFileAttributesExW)(LPCWSTR,GET_FILEEX_INFO_LEVELS,LPVOID);
typedef HANDLE (WINAPI *PFN_FindFirstFileW)(LPCWSTR,LPWIN32_FIND_DATAW);
typedef HANDLE (WINAPI *PFN_FindFirstFileA)(LPCSTR,LPWIN32_FIND_DATAA);
typedef BOOL   (WINAPI *PFN_SetWindowTextW)(HWND,LPCWSTR);
typedef BOOL   (WINAPI *PFN_SetDlgItemTextW)(HWND,int,LPCWSTR);
typedef int    (WINAPI *PFN_GetWindowTextW)(HWND,LPWSTR,int);
typedef int    (WINAPI *PFN_MessageBoxW)(HWND,LPCWSTR,LPCWSTR,UINT);
typedef INT_PTR (WINAPI *PFN_DialogBoxIndirectParamW)(HINSTANCE,LPCDLGTEMPLATEW,HWND,DLGPROC,LPARAM);
typedef INT_PTR (WINAPI *PFN_DialogBoxParamW)(HINSTANCE,LPCWSTR,HWND,DLGPROC,LPARAM);
typedef HWND (WINAPI *PFN_CreateDialogIndirectParamW)(HINSTANCE,LPCDLGTEMPLATEW,HWND,DLGPROC,LPARAM);
typedef HWND (WINAPI *PFN_CreateDialogParamW)(HINSTANCE,LPCWSTR,HWND,DLGPROC,LPARAM);

static PFN_CreateFileW          Real_CreateFileW;
static PFN_CreateFileA          Real_CreateFileA;
static PFN_GetFileAttributesW   Real_GetFileAttributesW;
static PFN_GetFileAttributesA   Real_GetFileAttributesA;
static PFN_GetFileAttributesExW Real_GetFileAttributesExW;
static PFN_FindFirstFileW       Real_FindFirstFileW;
static PFN_FindFirstFileA       Real_FindFirstFileA;
static PFN_SetWindowTextW       Real_SetWindowTextW;
static PFN_SetDlgItemTextW      Real_SetDlgItemTextW;
static PFN_GetWindowTextW       Real_GetWindowTextW;
static PFN_MessageBoxW          Real_MessageBoxW;
static PFN_DialogBoxIndirectParamW Real_DialogBoxIndirectParamW;
static PFN_DialogBoxParamW      Real_DialogBoxParamW;
static PFN_CreateDialogIndirectParamW Real_CreateDialogIndirectParamW;
static PFN_CreateDialogParamW   Real_CreateDialogParamW;

static void translate_dialog_and_children(HWND hwnd);

static int file_exists_w(LPCWSTR path) { return Real_GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES; }

static wchar_t *wcsistr(const wchar_t *hay, const wchar_t *needle) {
    size_t nl = lstrlenW(needle);
    if (!nl) return (wchar_t*)hay;
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < nl) {
            wchar_t a = hay[i], b = needle[i];
            if (a >= L'A' && a <= L'Z') a += 32;
            if (b >= L'A' && b <= L'Z') b += 32;
            if (a != b) break;
            i++;
        }
        if (i == nl) return (wchar_t*)hay;
    }
    return NULL;
}
static char *stristr(const char *hay, const char *needle) {
    size_t nl = lstrlenA(needle);
    if (!nl) return (char*)hay;
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < nl) {
            char a = hay[i], b = needle[i];
            if (a >= 'A' && a <= 'Z') a += 32;
            if (b >= 'A' && b <= 'Z') b += 32;
            if (a != b) break;
            i++;
        }
        if (i == nl) return (char*)hay;
    }
    return NULL;
}
static int wstreq(const wchar_t *a, const wchar_t *b) { return a && b && lstrcmpW(a,b)==0; }

static int has_arg(const char *name) {
    const char *cmd = GetCommandLineA();
    if (cmd && stristr(cmd, name)) return 1;
    char env[64];
    DWORD n = GetEnvironmentVariableA("clannad_lang", env, sizeof(env));
    if (n == 0 || n >= sizeof(env)) n = GetEnvironmentVariableA("CLANNAD_LANG", env, sizeof(env));
    if (n == 0 || n >= sizeof(env)) return 0;
    if (lstrcmpiA(name, "clannad_lang=jp") == 0 || lstrcmpiA(name, "--clannad-jp") == 0 || lstrcmpiA(name, "-clannad_jp") == 0)
        return lstrcmpiA(env, "jp") == 0;
    if (lstrcmpiA(name, "clannad_lang=cn") == 0 || lstrcmpiA(name, "--clannad-cn") == 0 || lstrcmpiA(name, "-clannad_cn") == 0)
        return lstrcmpiA(env, "cn") == 0;
    return 0;
}

typedef struct { const wchar_t *cn; const wchar_t *jp; } StrMap;
static const StrMap kStrMap[] = {
    { L"要更改字体吗？\n\n※若选择“否”，将不会显示文字。", L"フォントを変更しますか？\n\n※「いいえ」を選択すると、文字は表示されません。" },
    { L"未发现字体。（或字体已损坏。）\n\n\u3000\u3000现在的字体：", L"フォントが見つかりません。（またはフォントが破損しています。）\n\n\u3000\u3000現在のフォント：" },
    { L"现在的字体：", L"現在のフォント：" },
    { L"现在的字体 :", L"現在のフォント：" },
    { L"字体：", L"フォント：" },
    { L"字体 :", L"フォント：" },
    { L"字体设置", L"フォント設定" },
    { L"字体设定", L"フォント設定" },
    { L"字体选择", L"フォント選択" },
    { L"选择字体", L"フォント選択" },
    { L"更改字体", L"フォント変更" },
    { L"变更字体", L"フォント変更" },
    { L"显示比例字体", L"プロポーショナルフォントを表示" },
    { L"顯示比例字體", L"プロポーショナルフォントを表示" },
    { L"※使用比例字体可能导致文本排版显示的异常。", L"※プロポーショナルフォントを使用すると表示が崩れる場合があります。" },
    { L"※使用比例字體可能導致文本排版顯示的異常。", L"※プロポーショナルフォントを使用すると表示が崩れる場合があります。" },
    { L"文字加粗", L"太字" },
    { L"无阴影", L"影なし" },
    { L"無陰影", L"影なし" },
    { L"有阴影", L"影あり" },
    { L"有陰影", L"影あり" },
    { L"显示文字描边", L"縁取り" },
    { L"顯示文字描邊", L"縁取り" },
    { L"描边+阴影", L"縁取り＋影" },
    { L"描邊+陰影", L"縁取り＋影" },
    { L"“请不要将天堂…分开”", L"「天国をふたつに、わけないでください」" },
    { L"“请不要将天堂...分开”", L"「天国をふたつに、わけないでください」" },
    { L"“請不要將天堂…分開”", L"「天国をふたつに、わけないでください」" },
    { L"“請不要將天堂...分開”", L"「天国をふたつに、わけないでください」" },
    { L"请不要将天堂…分开", L"天国をふたつに、わけないでください" },
    { L"请不要将天堂...分开", L"天国をふたつに、わけないでください" },
    { L"請不要將天堂…分開", L"天国をふたつに、わけないでください" },
    { L"請不要將天堂...分開", L"天国をふたつに、わけないでください" },
    { L"環境設置", L"環境設定" },
    { L"縮短播放時間（快進聲音）設置", L"再生時間短縮（音声早送り）設定" },
    { L"??播放方式?置", L"ムービー再生方法設定" },
    { L"對話框背景?色設置", L"ウィンドウ背景色設定" },
    { L"畫面設置", L"画面設定" },
    { L"系統設置", L"システム設定" },
    { L"推特", L"Twitter" },
    { L"影片播放设置", L"ムービー再生設定" },
    { L"影片播放设定", L"ムービー再生設定" },
    { L"影片播放方式", L"ムービー再生方法" },
    { L"影片播放方式设置", L"ムービー再生方法設定" },
    { L"视频播放方式设定", L"ムービー再生方法設定" },
    { L"影片播放", L"ムービー再生" },
    { L"视频播放设置", L"ムービー再生設定" },
    { L"视频播放设定", L"ムービー再生設定" },
    { L"视频播放方式", L"ムービー再生方法" },
    { L"视频播放方式设置", L"ムービー再生方法設定" },
    { L"该设置可以更改视频播放方式。游戏过程中，如果无法正常播放视频，请更改该设置。\n※如果没有问题，请继续设置为「Windows Media Player」。", L"この設定でムービーの再生方法を変更できます。ゲーム中に正常に再生できない場合は、設定を変更してください。\n※問題がなければ、「Windows Media Player」のままにしてください。" },
    { L"如果设置为「Windows Media Player」，需要 Windows Media Player 9 以上的版本。\n※因使用配置不同，可能无法正常运行。", L"「Windows Media Player」に設定する場合は、Windows Media Player 9 以上が必要です。\n※環境によっては正常に動作しない場合があります。" },
    { L"該設置可以更改影片播放方式。遊戲過程中，如果無法正常播放影片，請更改該設置。\n※如果沒有問題，請繼續設置為「Windows Media Player」。", L"この設定でムービーの再生方法を変更できます。ゲーム中に正常に再生できない場合は、設定を変更してください。\n※問題がなければ、「Windows Media Player」のままにしてください。" },
    { L"如果設置為「Windows Media Player」，需要Windows Media Player 9以上の版本。\n※因使用配置不同，可能無法正常運行。", L"「Windows Media Player」に設定する場合は、Windows Media Player 9 以上が必要です。\n※環境によっては正常に動作しない場合があります。" },
    { L"Windows Media Player (标准)", L"Windows Media Player（標準）" },
    { L"Windows Media Player (標準)", L"Windows Media Player（標準）" },
    { L"确定", L"OK" },
    { L"確定", L"OK" },
    { L"取消", L"キャンセル" },
    { L"关闭", L"閉じる" },
    { L"關閉", L"閉じる" },
    { L"是(&Y)", L"はい(&Y)" },
    { L"否(&N)", L"いいえ(&N)" },
    { L"非(&N)", L"いいえ(&N)" },
    { L"是(Y)", L"はい(Y)" },
    { L"否(N)", L"いいえ(N)" },
    { L"非(N)", L"いいえ(N)" },
    { L"&Yes", L"はい(&Y)" },
    { L"&No", L"いいえ(&N)" },
    { L"是", L"はい" },
    { L"否", L"いいえ" },
    { L"非", L"いいえ" },
    { L"画面设置", L"画面設定" },
    { L"畫面設置", L"画面設定" },
    { L"画面模式", L"画面モード" },
    { L"畫面模式", L"画面モード" },
    { L"全屏幕", L"フルスクリーン" },
    { L"全螢幕", L"フルスクリーン" },
    { L"窗口", L"ウィンドウ" },
    { L"視窗", L"ウィンドウ" },
    { L"窗口尺寸", L"ウィンドウサイズ" },
    { L"全屏分辨率", L"フルスクリーン解像度" },
    { L"全屏详细设置", L"フルスクリーン詳細設定" },
    { L"显示模式", L"表示モード" },
    { L"顯示模式", L"表示モード" },
    { L"显示 : ", L"表示 : " },
    { L"顯示 : ", L"表示 : " },
    { L"分辨率 : ", L"解像度 : " },
    { L"解析度 : ", L"解像度 : " },
    { L"更改分辨率", L"解像度変更" },
    { L"更改解析度", L"解像度変更" },
    { L"关于分辨率", L"解像度について" },
    { L"關於解析度", L"解像度について" },
    { L"原尺寸", L"原寸" },
    { L"匹配全屏幕", L"フルスクリーンに合わせる" },
    { L"匹配上下画面", L"上下に合わせる" },
    { L"匹配左右画面", L"左右に合わせる" },
    { L"自定义设置", L"カスタム設定" },
    { L"自定義設置", L"カスタム設定" },
    { L"同步倍率", L"倍率を同期" },
    { L"上下移动 : ", L"上下移動 : " },
    { L"上下移動 ：", L"上下移動 ：" },
    { L"左右移动 : ", L"左右移動 : " },
    { L"左右移動 ：", L"左右移動 ：" },
    { L"恢复原始设置", L"初期設定に戻す" },
    { L"恢復原始設置", L"初期設定に戻す" },
    { L"恢復原始設定", L"初期設定に戻す" },
    { L"环境设置", L"環境設定" },
    { L"系统设置", L"システム設定" },
    { L"对话框背景颜色", L"ウィンドウ背景色" },
    { L"對話框背景?色", L"ウィンドウ背景色" },
    { L"对话框背景颜色设置", L"ウィンドウ背景色設定" },
    { L"透明度", L"透明度" },
    { L"全部初始化", L"すべて初期化" },
    { L"缩短播放时间（快进声音）", L"再生時間短縮（音声早送り）" },
    { L"縮短播放時間（快進聲音）", L"再生時間短縮（音声早送り）" },
    { L"缩短播放时间（快进声音）设置", L"再生時間短縮（音声早送り）設定" },
    { L"标准", L"標準" },
    { L"更改正常阅读文本的播放速度", L"通常のテキスト再生速度を変更" },
    { L"更改正常閱讀文本的播放速度", L"通常のテキスト再生速度を変更" },
    { L"更改自动模式时的播放速度", L"オートモード時の再生速度を変更" },
    { L"更改自動模式時的播放速度", L"オートモード時の再生速度を変更" },
    { L"更改重播声音时的播放速度", L"音声リプレイ時の再生速度を変更" },
    { L"更改重播聲音時的播放速度", L"音声リプレイ時の再生速度を変更" },
    { L"音量设置", L"音量設定" },
    { L"文本设置", L"テキスト設定" },
    { L"文字速度设置", L"文字速度設定" },
    { L"声音设定", L"音声設定" },
    { L"声音模式设定", L"音声モード設定" },
    { L"角色语音设定", L"キャラクターボイス設定" },
    { L"BGM淡出设置", L"BGMフェードアウト設定" },
    { NULL, NULL }
};

static const wchar_t *translate_w(const wchar_t *s) {
    static __thread wchar_t buf[1024];
    if (g_jp != 1 || !s) return s;
    for (int i = 0; kStrMap[i].cn; i++) {
        if (wstreq(s, kStrMap[i].cn)) return kStrMap[i].jp;
    }
    {
        const wchar_t *pfx = L"现在的字体：";
        const wchar_t *pfx2 = L"現在のフォント：";
        size_t n = lstrlenW(pfx);
        if (wcsncmp(s, pfx, n) == 0) {
            lstrcpynW(buf, pfx2, 1024);
            lstrcpynW(buf + lstrlenW(buf), s + n, 1024 - lstrlenW(buf));
            return buf;
        }
    }
    {
        const wchar_t *pfx = L"未发现字体。（或字体已损坏。）\n\n\u3000\u3000现在的字体：";
        const wchar_t *pfx2 = L"フォントが見つかりません。（またはフォントが破損しています。）\n\n\u3000\u3000現在のフォント：";
        size_t n = lstrlenW(pfx);
        if (wcsncmp(s, pfx, n) == 0) {
            lstrcpynW(buf, pfx2, 1024);
            lstrcpynW(buf + lstrlenW(buf), s + n, 1024 - lstrlenW(buf));
            return buf;
        }
    }
    return s;
}

static void translate_window_text(HWND hwnd) {
    if (!Real_GetWindowTextW || !Real_SetWindowTextW) return;
    wchar_t text[1024];
    if (!Real_GetWindowTextW(hwnd, text, 1024)) return;
    const wchar_t *translated = translate_w(text);
    if (translated != text) Real_SetWindowTextW(hwnd, translated);
}

static BOOL CALLBACK translate_child_text(HWND hwnd, LPARAM unused) {
    (void)unused;
    translate_window_text(hwnd);
    return TRUE;
}

static void translate_dialog_and_children(HWND hwnd) {
    if (InterlockedCompareExchange(&g_in_translate, 1, 0) != 0) return;
    translate_window_text(hwnd);
    EnumChildWindows(hwnd, translate_child_text, 0);
    InterlockedExchange(&g_in_translate, 0);
}

static LRESULT CALLBACK CbtFilterHook(int nCode, WPARAM wParam, LPARAM lParam) {
    if (nCode == HCBT_ACTIVATE && g_jp == 1) {
        HWND hwnd = (HWND)wParam;
        wchar_t cls[32];
        if (GetClassNameW(hwnd, cls, 32) && lstrcmpW(cls, L"#32770") == 0) {
            translate_dialog_and_children(hwnd);
        }
    }
    return CallNextHookEx(g_cbtHook, nCode, wParam, lParam);
}

static void ensure_cbt_hook(void) {
    if (!g_cbtHook && g_jp == 1) {
        g_cbtHook = SetWindowsHookExW(WH_CBT, CbtFilterHook, g_hInst, 0);
        if (!g_cbtHook) {
            g_cbtHook = SetWindowsHookExW(WH_CBT, CbtFilterHook, NULL, GetCurrentThreadId());
        }
    }
}

int WINAPI Hook_MessageBoxW(HWND h, LPCWSTR text, LPCWSTR cap, UINT t) {
    ensure_cbt_hook();
    if (!Real_MessageBoxW) return MessageBoxW(h, translate_w(text), translate_w(cap), t);
    return Real_MessageBoxW(h, translate_w(text), translate_w(cap), t);
}

INT_PTR WINAPI Hook_DialogBoxIndirectParamW(HINSTANCE instance, LPCDLGTEMPLATEW templ, HWND parent, DLGPROC proc, LPARAM param) {
    ensure_cbt_hook();
    return Real_DialogBoxIndirectParamW(instance, templ, parent, proc, param);
}

INT_PTR WINAPI Hook_DialogBoxParamW(HINSTANCE instance, LPCWSTR templ, HWND parent, DLGPROC proc, LPARAM param) {
    ensure_cbt_hook();
    return Real_DialogBoxParamW(instance, templ, parent, proc, param);
}

HWND WINAPI Hook_CreateDialogIndirectParamW(HINSTANCE instance, LPCDLGTEMPLATEW templ, HWND parent, DLGPROC proc, LPARAM param) {
    ensure_cbt_hook();
    HWND hwnd = Real_CreateDialogIndirectParamW(instance, templ, parent, proc, param);
    if (hwnd && g_jp == 1) translate_dialog_and_children(hwnd);
    return hwnd;
}

HWND WINAPI Hook_CreateDialogParamW(HINSTANCE instance, LPCWSTR templ, HWND parent, DLGPROC proc, LPARAM param) {
    ensure_cbt_hook();
    HWND hwnd = Real_CreateDialogParamW(instance, templ, parent, proc, param);
    if (hwnd && g_jp == 1) translate_dialog_and_children(hwnd);
    return hwnd;
}

static int rewrite_w(LPCWSTR in, wchar_t *out, size_t cap) {
    if (g_jp != 1 || !in) return 0;
    wchar_t *pos = wcsistr(in, L"GameexeZH.dat");
    if (pos) {
        size_t pre = (size_t)(pos - in); const wchar_t *suf = L"GameexeJP.dat";
        if (pre + lstrlenW(suf) + 1 >= cap) return 0;
        lstrcpynW(out, in, (int)pre + 1); lstrcatW(out, suf);
        return file_exists_w(out) ? 1 : 0;
    }
    pos = wcsistr(in, L"NotoSansMonoCJKsc-Regular.otf");
    if (pos) {
        size_t pre = (size_t)(pos - in); const wchar_t *suf = L"NotoSansMonoCJKjp-Regular.otf";
        if (pre + lstrlenW(suf) + 1 >= cap) return 0;
        lstrcpynW(out, in, (int)pre + 1); lstrcatW(out, suf);
        return file_exists_w(out) ? 1 : 0;
    }
    const wchar_t *froms[] = { L"\\g00\\", L"/g00\\", L"\\g00/", L"/g00/", L"\\dat\\", L"/dat\\", L"\\dat/", L"/dat/", NULL };
    const wchar_t *tos[]   = { L"\\g00_jp\\", L"/g00_jp\\", L"\\g00_jp/", L"/g00_jp/", L"\\dat_jp\\", L"/dat_jp\\", L"\\dat_jp/", L"/dat_jp/", NULL };
    for (int i = 0; froms[i]; i++) {
        wchar_t *p = wcsistr(in, froms[i]); if (!p) continue;
        size_t pre = (size_t)(p - in), fl = lstrlenW(froms[i]), rest = lstrlenW(p + fl);
        if (pre + lstrlenW(tos[i]) + rest + 1 >= cap) return 0;
        lstrcpynW(out, in, (int)pre + 1); lstrcatW(out, tos[i]); lstrcatW(out, p + fl);
        return file_exists_w(out) ? 1 : 0;
    }
    return 0;
}
static int rewrite_a(LPCSTR in, char *out, size_t cap) {
    if (g_jp != 1 || !in) return 0;
    char *pos = stristr(in, "GameexeZH.dat");
    if (pos) {
        size_t pre = (size_t)(pos - in); const char *suf = "GameexeJP.dat";
        if (pre + lstrlenA(suf) + 1 >= cap) return 0;
        lstrcpynA(out, in, (int)pre + 1); lstrcatA(out, suf);
        wchar_t w[MAX_PATH*2]; MultiByteToWideChar(CP_ACP,0,out,-1,w,MAX_PATH*2);
        return file_exists_w(w) ? 1 : 0;
    }
    pos = stristr(in, "NotoSansMonoCJKsc-Regular.otf");
    if (pos) {
        size_t pre = (size_t)(pos - in); const char *suf = "NotoSansMonoCJKjp-Regular.otf";
        if (pre + lstrlenA(suf) + 1 >= cap) return 0;
        lstrcpynA(out, in, (int)pre + 1); lstrcatA(out, suf);
        wchar_t w[MAX_PATH*2]; MultiByteToWideChar(CP_ACP,0,out,-1,w,MAX_PATH*2);
        return file_exists_w(w) ? 1 : 0;
    }
    const char *froms[] = { "\\g00\\", "/g00\\", "\\g00/", "/g00/", "\\dat\\", "/dat\\", "\\dat/", "/dat/", NULL };
    const char *tos[]   = { "\\g00_jp\\", "/g00_jp\\", "\\g00_jp/", "/g00_jp/", "\\dat_jp\\", "/dat_jp\\", "\\dat_jp/", "/dat_jp/", NULL };
    for (int i = 0; froms[i]; i++) {
        char *p = stristr(in, froms[i]); if (!p) continue;
        size_t pre = (size_t)(p - in), fl = lstrlenA(froms[i]), rest = lstrlenA(p + fl);
        if (pre + lstrlenA(tos[i]) + rest + 1 >= cap) return 0;
        lstrcpynA(out, in, (int)pre + 1); lstrcatA(out, tos[i]); lstrcatA(out, p + fl);
        wchar_t w[MAX_PATH*2]; MultiByteToWideChar(CP_ACP,0,out,-1,w,MAX_PATH*2);
        return file_exists_w(w) ? 1 : 0;
    }
    return 0;
}

HANDLE WINAPI Hook_CreateFileW(LPCWSTR n,DWORD a,DWORD b,LPSECURITY_ATTRIBUTES c,DWORD d,DWORD e,HANDLE f){
    wchar_t buf[MAX_PATH*2]; if (rewrite_w(n,buf,MAX_PATH*2)) n=buf; return Real_CreateFileW(n,a,b,c,d,e,f);
}
HANDLE WINAPI Hook_CreateFileA(LPCSTR n,DWORD a,DWORD b,LPSECURITY_ATTRIBUTES c,DWORD d,DWORD e,HANDLE f){
    char buf[MAX_PATH*2]; if (rewrite_a(n,buf,MAX_PATH*2)) n=buf; return Real_CreateFileA(n,a,b,c,d,e,f);
}
DWORD WINAPI Hook_GetFileAttributesW(LPCWSTR n){
    wchar_t buf[MAX_PATH*2]; if (rewrite_w(n,buf,MAX_PATH*2)) n=buf; return Real_GetFileAttributesW(n);
}
DWORD WINAPI Hook_GetFileAttributesA(LPCSTR n){
    char buf[MAX_PATH*2]; if (rewrite_a(n,buf,MAX_PATH*2)) n=buf; return Real_GetFileAttributesA(n);
}
BOOL WINAPI Hook_GetFileAttributesExW(LPCWSTR n,GET_FILEEX_INFO_LEVELS l,LPVOID i){
    wchar_t buf[MAX_PATH*2]; if (rewrite_w(n,buf,MAX_PATH*2)) n=buf; return Real_GetFileAttributesExW(n,l,i);
}
HANDLE WINAPI Hook_FindFirstFileW(LPCWSTR n,LPWIN32_FIND_DATAW d){
    wchar_t buf[MAX_PATH*2]; if (rewrite_w(n,buf,MAX_PATH*2)) n=buf; return Real_FindFirstFileW(n,d);
}
HANDLE WINAPI Hook_FindFirstFileA(LPCSTR n,LPWIN32_FIND_DATAA d){
    char buf[MAX_PATH*2]; if (rewrite_a(n,buf,MAX_PATH*2)) n=buf; return Real_FindFirstFileA(n,d);
}

BOOL WINAPI Hook_SetWindowTextW(HWND h,LPCWSTR s){
    if (!Real_SetWindowTextW) return 0;
    return Real_SetWindowTextW(h, translate_w(s));
}
BOOL WINAPI Hook_SetDlgItemTextW(HWND h,int id,LPCWSTR s){
    if (!Real_SetDlgItemTextW) return 0;
    return Real_SetDlgItemTextW(h,id, translate_w(s));
}

static int iat_hook(HMODULE mod, const char *dll, const char *func, void *hook, void **orig) {
    BYTE *base = (BYTE*)mod;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER*)base; if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew); if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT]; if (!dir.VirtualAddress) return 0;
    IMAGE_IMPORT_DESCRIPTOR *imp = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress);
    for (; imp->Name; imp++) {
        const char *name = (const char*)(base + imp->Name), *a=name,*b=dll; int ok=1;
        while(*a&&*b){char ca=*a++,cb=*b++; if(ca>='A'&&ca<='Z')ca+=32; if(cb>='A'&&cb<='Z')cb+=32; if(ca!=cb){ok=0;break;}} if(!ok||*a||*b) continue;
        IMAGE_THUNK_DATA *oft=(IMAGE_THUNK_DATA*)(base+imp->OriginalFirstThunk), *ft=(IMAGE_THUNK_DATA*)(base+imp->FirstThunk); if(!imp->OriginalFirstThunk) oft=ft;
        for (; oft->u1.AddressOfData; oft++, ft++) {
            if (IMAGE_SNAP_BY_ORDINAL(oft->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME *ibn=(IMAGE_IMPORT_BY_NAME*)(base+oft->u1.AddressOfData);
            const char *fn=(const char*)ibn->Name,*x=fn,*y=func; int eq=1; while(*x&&*y){if(*x++!=*y++){eq=0;break;}} if(!eq||*x||*y) continue;
            DWORD oldProt; VirtualProtect(&ft->u1.Function,sizeof(void*),PAGE_READWRITE,&oldProt);
            if (orig && !*orig) {
                *orig = (void*)ft->u1.Function;
            }
            ft->u1.Function = (ULONG_PTR)hook;
            VirtualProtect(&ft->u1.Function, sizeof(void*), oldProt, &oldProt);
            return 1;
        }
    } return 0;
}

static void install_file_hooks(void) {
    HMODULE exe = GetModuleHandleW(NULL);
    iat_hook(exe,"KERNEL32.dll","CreateFileW",(void*)Hook_CreateFileW,(void**)(void*)&Real_CreateFileW);
    iat_hook(exe,"KERNEL32.dll","CreateFileA",(void*)Hook_CreateFileA,(void**)(void*)&Real_CreateFileA);
    iat_hook(exe,"KERNEL32.dll","GetFileAttributesW",(void*)Hook_GetFileAttributesW,(void**)(void*)&Real_GetFileAttributesW);
    iat_hook(exe,"KERNEL32.dll","GetFileAttributesA",(void*)Hook_GetFileAttributesA,(void**)(void*)&Real_GetFileAttributesA);
    iat_hook(exe,"KERNEL32.dll","GetFileAttributesExW",(void*)Hook_GetFileAttributesExW,(void**)(void*)&Real_GetFileAttributesExW);
    iat_hook(exe,"KERNEL32.dll","FindFirstFileW",(void*)Hook_FindFirstFileW,(void**)(void*)&Real_FindFirstFileW);
    iat_hook(exe,"KERNEL32.dll","FindFirstFileA",(void*)Hook_FindFirstFileA,(void**)(void*)&Real_FindFirstFileA);
    HMODULE k32 = GetModuleHandleW(L"KERNEL32.dll");
    if(!Real_CreateFileW) Real_CreateFileW=(PFN_CreateFileW)(void*)GetProcAddress(k32,"CreateFileW");
    if(!Real_CreateFileA) Real_CreateFileA=(PFN_CreateFileA)(void*)GetProcAddress(k32,"CreateFileA");
    if(!Real_GetFileAttributesW) Real_GetFileAttributesW=(PFN_GetFileAttributesW)(void*)GetProcAddress(k32,"GetFileAttributesW");
    if(!Real_GetFileAttributesA) Real_GetFileAttributesA=(PFN_GetFileAttributesA)(void*)GetProcAddress(k32,"GetFileAttributesA");
    if(!Real_GetFileAttributesExW) Real_GetFileAttributesExW=(PFN_GetFileAttributesExW)(void*)GetProcAddress(k32,"GetFileAttributesExW");
    if(!Real_FindFirstFileW) Real_FindFirstFileW=(PFN_FindFirstFileW)(void*)GetProcAddress(k32,"FindFirstFileW");
    if(!Real_FindFirstFileA) Real_FindFirstFileA=(PFN_FindFirstFileA)(void*)GetProcAddress(k32,"FindFirstFileA");
}

static void install_ui_hooks(void) {
    HMODULE exe = GetModuleHandleW(NULL);
    HMODULE u32 = GetModuleHandleW(L"USER32.dll");
    iat_hook(exe,"USER32.dll","SetWindowTextW",(void*)Hook_SetWindowTextW,(void**)(void*)&Real_SetWindowTextW);
    iat_hook(exe,"USER32.dll","SetDlgItemTextW",(void*)Hook_SetDlgItemTextW,(void**)(void*)&Real_SetDlgItemTextW);
    iat_hook(exe,"USER32.dll","MessageBoxW",(void*)Hook_MessageBoxW,(void**)(void*)&Real_MessageBoxW);
    iat_hook(exe,"USER32.dll","DialogBoxIndirectParamW",(void*)Hook_DialogBoxIndirectParamW,(void**)(void*)&Real_DialogBoxIndirectParamW);
    iat_hook(exe,"USER32.dll","DialogBoxParamW",(void*)Hook_DialogBoxParamW,(void**)(void*)&Real_DialogBoxParamW);
    iat_hook(exe,"USER32.dll","CreateDialogIndirectParamW",(void*)Hook_CreateDialogIndirectParamW,(void**)(void*)&Real_CreateDialogIndirectParamW);
    iat_hook(exe,"USER32.dll","CreateDialogParamW",(void*)Hook_CreateDialogParamW,(void**)(void*)&Real_CreateDialogParamW);
    if(!Real_SetWindowTextW) Real_SetWindowTextW=(PFN_SetWindowTextW)(void*)GetProcAddress(u32,"SetWindowTextW");
    if(!Real_SetDlgItemTextW) Real_SetDlgItemTextW=(PFN_SetDlgItemTextW)(void*)GetProcAddress(u32,"SetDlgItemTextW");
    if(!Real_GetWindowTextW) Real_GetWindowTextW=(PFN_GetWindowTextW)(void*)GetProcAddress(u32,"GetWindowTextW");
    if(!Real_MessageBoxW) Real_MessageBoxW=(PFN_MessageBoxW)(void*)GetProcAddress(u32,"MessageBoxW");
    if(!Real_DialogBoxIndirectParamW) Real_DialogBoxIndirectParamW=(PFN_DialogBoxIndirectParamW)(void*)GetProcAddress(u32,"DialogBoxIndirectParamW");
    if(!Real_DialogBoxParamW) Real_DialogBoxParamW=(PFN_DialogBoxParamW)(void*)GetProcAddress(u32,"DialogBoxParamW");
    if(!Real_CreateDialogIndirectParamW) Real_CreateDialogIndirectParamW=(PFN_CreateDialogIndirectParamW)(void*)GetProcAddress(u32,"CreateDialogIndirectParamW");
    if(!Real_CreateDialogParamW) Real_CreateDialogParamW=(PFN_CreateDialogParamW)(void*)GetProcAddress(u32,"CreateDialogParamW");
    ensure_cbt_hook();
}

static void choose_language(void) {
    wchar_t test[MAX_PATH]; lstrcpyW(test,g_gameDir); lstrcatW(test,L"\\g00_jp");
    DWORD attr = GetFileAttributesW(test);
    if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY)) { g_jp = 0; return; }
    if (has_arg("clannad_lang=jp") || has_arg("--clannad-jp") || has_arg("-clannad_jp")) { g_jp = 1; return; }
    if (has_arg("clannad_lang=cn") || has_arg("--clannad-cn") || has_arg("-clannad_cn")) { g_jp = 0; return; }
    int r = MessageBoxW(NULL,
        L"CLANNAD をどちらで起動しますか？\n\n"
        L"「はい」: 日本語版 (g00_jp / dat_jp)\n"
        L"「いいえ」: 中国語版 (元のまま)\n"
        L"「キャンセル」: 起動しない",
        L"CLANNAD 起動選択",
        MB_YESNOCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST);
    if (r == IDCANCEL) { ExitProcess(0); return; }
    g_jp = (r == IDYES) ? 1 : 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_hInst = hinst;
        DisableThreadLibraryCalls(hinst);
        GetModuleFileNameW(NULL, g_gameDir, MAX_PATH); PathRemoveFileSpecW(g_gameDir);
        load_real();
        install_file_hooks();
        choose_language();
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_cbtHook) {
            UnhookWindowsHookEx(g_cbtHook);
            g_cbtHook = NULL;
        }
    }
    return TRUE;
}
