package hx.well.config;
class DatabaseConfig implements IConfig {
    public function new() {}
    public var connections:haxe.ds.StringMap<ConnectionTypedef> = new haxe.ds.StringMap();
}
