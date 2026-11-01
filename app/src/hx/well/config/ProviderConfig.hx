package hx.well.config;
class ProviderConfig implements IConfig {
    public function new() {}
    public function get():Array<Class<hx.well.provider.AbstractProvider>> { return [hx.well.provider.BenchmarkProvider]; }
}
