package benchapp;

import hx.well.http.Request;

#if ((cpp || jvm) && bench_crossbyte)
@:access(hx.well.http.driver.crossbyte.CrossbyteDriverContext)
@:access(hx.well.http.driver.crossbyte.internal.ResponseBridge)
#end
#if (cpp && bench_crossbyte)
@:access(crossbyte.core.CrossByte)
@:access(crossbyte._internal.socket.NativeSocketRegistry)
#end
#if bench_libuv
// Keep this tiny diagnostic independent of platform headers included by uv.h.
@:cppFileCode('extern "C" const char* uv_version_string(void);')
#end
#if (bench_libuv && mac)
// On case-insensitive macOS volumes, generated uuid/Uuid.h shadows the
// SDK's uuid/uuid.h used by pwd.h through uv.h. Supply the SDK type first.
@:buildXml("<files id='haxe'><compilerflag value='-include'/><compilerflag value='sys/_types/_uuid_t.h'/></files>")
#end
class BackendProbe {
    /** Read the request's actual socket-owner runtime, outside measured traffic. */
    public static function inspect(request:Request):String {
        #if (cpp && bench_crossbyte)
        var context:hx.well.http.driver.crossbyte.CrossbyteDriverContext = cast request.context;
        var runtime = context.bridge.runtime;
        if(runtime == null) throw new haxe.Exception("Missing request I/O runtime");
        var ready = new sys.thread.Lock();
        var result = "unavailable";
        if(!runtime.post(() -> {
            #if bench_libuv
            result = crossbyte.libuv.LibuvPoll.isActive(runtime) ? "libuv" : "builtin-fallback";
            #else
            var registry = runtime.__socketRegistry;
            result = registry != null && Std.isOfType(registry.__poll, crossbyte._internal.socket.HaxePollBackend)
                ? "builtin" : "unknown";
            #end
            ready.release();
        }) || !ready.wait(2)) throw new haxe.Exception("Could not inspect the request I/O backend");
        return result;
        #else
        return "not-applicable";
        #end
    }

    /** Ask the actual request socket owner, outside timed traffic. */
    public static function ioVirtualThread(request:Request):Null<Bool> {
        #if (jvm && bench_crossbyte)
        var context:hx.well.http.driver.crossbyte.CrossbyteDriverContext = cast request.context;
        var runtime = context.bridge.runtime;
        if(runtime == null) throw new haxe.Exception("Missing request I/O runtime");
        var ready = new sys.thread.Lock();
        var result:Null<Bool> = null;
        if(!runtime.post(() -> {
            result = jvm.thread.VirtualThread.VirtualThreadExtern.currentThread().isVirtual();
            ready.release();
        }) || !ready.wait(2)) throw new haxe.Exception("Could not inspect the request I/O thread");
        return result;
        #else
        return null;
        #end
    }

    public static function libuvVersion():String {
        #if bench_libuv
        return untyped __cpp__("::String(uv_version_string())");
        #else
        return "";
        #end
    }
}
