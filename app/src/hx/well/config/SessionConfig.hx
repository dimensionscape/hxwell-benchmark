package hx.well.config;
class SessionConfig implements IConfig {
    public function new() {}
    public var path:String = "session";
    public var lifetime:Int = 360;
    public var guards:haxe.ds.StringMap<Class<hx.well.auth.IAuthenticatable>> = new haxe.ds.StringMap();
}
