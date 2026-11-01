package hx.well.config;
class MiddlewareConfig implements IConfig {
    public function new() {}
    public function get():Array<Class<hx.well.middleware.AbstractMiddleware>> { return []; }
}
