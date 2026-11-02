package hx.well.provider;

import benchapp.Settings;
import hx.well.http.AbstractResponse;
import hx.well.http.JsonResponse;
import hx.well.http.Response;
import hx.well.http.StringResponse;
import hx.well.route.Route;

class BenchmarkProvider extends AbstractProvider {
    public function boot():Void {
        // Suppress per-request traces equally for all stock driver implementations.
        haxe.Log.trace = (_, ?position) -> {};
        #if bench_crossbyte
        crossbyte.utils.Logger.level = crossbyte.utils.LogLevel.OFF;
        #end
        Route.get("/health", req -> (new JsonResponse({
            run_id:Settings.runId, case_id:Settings.caseId, driver:Settings.driver,
            target:Settings.target, workers:Settings.workers, hashlink_mode:Settings.hashlinkMode,
            cpp_runtime:Settings.cppRuntime, cpp_runtime_version:Settings.cppRuntimeVersion,
            io_virtual_thread:benchapp.BackendProbe.ioVirtualThread(req),
            poll_backend:benchapp.BackendProbe.inspect(req), libuv_version:benchapp.BackendProbe.libuvVersion(),
            virtual_thread:#if jvm jvm.thread.VirtualThread.VirtualThreadExtern.currentThread().isVirtual() #else false #end
        }) : AbstractResponse));
        Route.get("/plaintext", _ -> persistent(new StringResponse("Hello, World!\n").header("Content-Type", "text/plain")));
        Route.get("/json", _ -> persistent(new JsonResponse({message:"Hello, World!", number:42})));
        Route.post("/echo", request -> persistent(new JsonResponse({message:request.input("message"), number:request.input("number")})));
    }

    static function persistent(response:Response):AbstractResponse {
        return response.header("Connection", "keep-alive");
    }
}
