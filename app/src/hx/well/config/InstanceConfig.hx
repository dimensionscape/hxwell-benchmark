package hx.well.config;

import benchapp.Settings;
import hx.well.server.instance.IInstance;

class InstanceConfig implements IConfig {
    public function new() {}
    public function get():Array<IInstance> {
        #if bench_crossbyte
        return [hx.well.http.driver.crossbyte.CrossbyteInstance.builder()
            .setHost("127.0.0.1").setPort(Settings.port).setPoolSize(Settings.workers)
            .setMaxConnections(Settings.connections).setMaxConcurrentRequests(Settings.connections)
            .configureHttp(http -> {
                http.runtimeCount = Settings.workers;
                http.rateLimitKey = _ -> null;
                http.compression.enabled = false;
                http.keepAliveMaxRequests = 0;
                http.keepAliveTimeout = 30;
                http.http2Enabled = false;
            }).build()];
        #elseif bench_undertow
        return [hx.well.http.driver.undertow.UndertowInstance.builder()
            .setHost("127.0.0.1").setPort(Settings.port)
            .setWorkerOption(benchapp.XnioOptions.WORKER_IO_THREADS, Settings.workers)
            .setWorkerOption(benchapp.XnioOptions.WORKER_TASK_CORE_THREADS, Settings.workers)
            .setWorkerOption(benchapp.XnioOptions.WORKER_TASK_MAX_THREADS, Settings.workers)
            .build()];
        #elseif js
        return [hx.well.http.driver.nodehttp.NodeHttpInstance.builder()
            .setHost("127.0.0.1").setPort(Settings.port).setPoolSize(Settings.workers).build()];
        #else
        return [hx.well.http.driver.socket.SocketInstance.builder()
            .setHost("127.0.0.1").setPort(Settings.port).setPoolSize(Settings.workers)
            .setMaxConnections(Settings.connections).build()];
        #end
    }
}
