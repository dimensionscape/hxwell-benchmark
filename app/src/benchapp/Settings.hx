package benchapp;

class Settings {
    public static var port:Int = integer("BENCH_PORT", 3000, 1, 65535);
    public static var workers:Int = integer("BENCH_WORKERS", 2, 1, 256);
    public static var connections:Int = integer("BENCH_MAX_CONNECTIONS", 1024, 1, 65535);
    public static var runId:String = Sys.getEnv("BENCH_RUN_ID") ?? "manual";
    public static var caseId:String = haxe.macro.Compiler.getDefine("bench_case");
    public static var driver:String = haxe.macro.Compiler.getDefine("bench_driver");
    public static var target:String = #if jvm "jvm" #elseif hl "hl" #elseif js "js" #elseif cpp "cpp" #else "unsupported" #end;
    public static var hashlinkMode:String = #if hl "jit" #else "" #end;
    public static var cppRuntime:String = #if (cpp && libhxcpp) "libhxcpp" #elseif cpp "hxcpp" #else "" #end;
    public static var cppRuntimeVersion:String = #if (cpp && libhxcpp) haxe.macro.Compiler.getDefine("libhxcpp") #else "" #end;

    static function integer(name:String, fallback:Int, minimum:Int, maximum:Int):Int {
        var text = Sys.getEnv(name);
        if(text == null) return fallback;
        var value = Std.parseInt(text);
        if(!~/^[0-9]+$/.match(text) || value == null || value < minimum || value > maximum)
            throw new haxe.Exception("Invalid benchmark setting " + name);
        return value;
    }
}
