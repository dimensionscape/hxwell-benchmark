package benchapp;

#if bench_undertow
import hx.well.http.driver.undertow.UndertowExtern.Option;

@:native("org.xnio.Options")
extern class XnioOptions {
    public static var WORKER_IO_THREADS:Option<Int>;
    public static var WORKER_TASK_CORE_THREADS:Option<Int>;
    public static var WORKER_TASK_MAX_THREADS:Option<Int>;
}
#end
