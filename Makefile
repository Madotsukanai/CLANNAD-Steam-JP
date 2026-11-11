CC = i686-w64-mingw32-gcc
CFLAGS = -Os -s -Wall -Wextra
LDFLAGS = -shared
LIBS = -lshlwapi -luser32 -lkernel32
TARGET = steam_api.dll
SRCS = steam_proxy.c proxy_stubs.S
DEF = steam_api.def

all: $(TARGET)

$(TARGET): $(SRCS) $(DEF)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRCS) $(DEF) $(LIBS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
