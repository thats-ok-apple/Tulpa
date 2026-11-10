using System;
using System.Diagnostics;
using System.IO;
using System.Text;

// A console subsystem executable: pipes, exit codes and Ctrl+C work for agents.
static class CLI {
    static string Quote(string value) {
        var result=new StringBuilder("\"");int slashes=0;
        foreach(char c in value) {
            if(c=='\\'){slashes++;continue;}
            if(c=='"'){result.Append('\\',slashes*2+1);result.Append(c);slashes=0;continue;}
            result.Append('\\',slashes);slashes=0;result.Append(c);
        }
        result.Append('\\',slashes*2);return result.Append('"').ToString();
    }
    static int Main(string[] args) {
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            var arguments=new StringBuilder("-X utf8 -B -m chatlocal.mcp_cli");
            foreach(string arg in args)arguments.Append(" ").Append(Quote(arg));
            var start=new ProcessStartInfo(Path.Combine(root,"runtime","python.exe"),arguments.ToString());
            start.WorkingDirectory=root;start.UseShellExecute=false;
            foreach(string key in new[]{"PYTHONHOME","PYTHONPATH","API_KEY","API_BASE","MODEL","REPLY_ONEBOT_URL","REPLY_ONEBOT_TOKEN","REPLY_ONEBOT_WS_URL","REPLY_ONEBOT_WS_TOKEN","DSH_HOME","DEEPSEEK_HARNESS_RUNTIME_MODE"})start.EnvironmentVariables.Remove(key);
            start.EnvironmentVariables["PYTHONUTF8"]="1";
            Console.CancelKeyPress+=(s,e)=>{e.Cancel=true;};
            using(var process=Process.Start(start)){process.WaitForExit();return process.ExitCode;}
        } catch {
            Console.Error.WriteLine("Tulpa CLI could not start. Please extract the complete MCP package.");return 1;
        }
    }
}
