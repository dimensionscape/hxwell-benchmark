package hx.well.config;

import benchapp.Settings;
import hx.well.server.instance.IInstance;

class InstanceConfig implements IConfig {
    public function new() {}
    public function get():Array<IInstance> {
        #if bench_crossbyte
        #if bench_libuv
        if(!crossbyte.libuv.LibuvPoll.install())
            throw new haxe.Exception("The libuv benchmark requires native libuv support");
        #end
        var builder = hx.well.http.driver.crossbyte.CrossbyteInstance.builder()
            .setHost("127.0.0.1").setPort(Settings.port);
        #if jvm
        builder.setMaxConnections(10000).setMaxConcurrentRequests(2000).setPoolSize(16);
        #elseif cpp
        // A worker waits for the I/O runtime to write each part of its
        // response, so one per CPU leaves the CPUs partly idle.
        builder.setMaxConnections(Settings.connections).setMaxConcurrentRequests(Settings.connections).setPoolSize(Settings.workers * 8);
        #else
        builder.setMaxConnections(Settings.connections).setMaxConcurrentRequests(Settings.connections).setPoolSize(Settings.workers);
        #end
        return [builder.configureHttp(http -> {
                #if jvm
                // Keep connection I/O on the listener runtime to avoid the
                // bounded handoff queue during bursts of new connections.
                http.runtimeCount = 0;
                #else
                http.runtimeCount = Settings.workers;
                #end
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
