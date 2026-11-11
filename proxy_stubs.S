    .text
    .extern _p

    .globl _proxy_SteamInternal_CreateInterface
_proxy_SteamInternal_CreateInterface:
    jmp *_p

    .globl _proxy_SteamAPI_GetHSteamPipe
_proxy_SteamAPI_GetHSteamPipe:
    jmp *(_p+4)

    .globl _proxy_SteamAPI_GetHSteamUser
_proxy_SteamAPI_GetHSteamUser:
    jmp *(_p+8)

    .globl _proxy_SteamAPI_RunCallbacks
_proxy_SteamAPI_RunCallbacks:
    jmp *(_p+12)

    .globl _proxy_SteamAPI_RegisterCallback
_proxy_SteamAPI_RegisterCallback:
    jmp *(_p+16)

    .globl _proxy_SteamAPI_Shutdown
_proxy_SteamAPI_Shutdown:
    jmp *(_p+20)

    .globl _proxy_SteamAPI_Init
_proxy_SteamAPI_Init:
    jmp *(_p+24)

    .globl _proxy_SteamAPI_UnregisterCallback
_proxy_SteamAPI_UnregisterCallback:
    jmp *(_p+28)
