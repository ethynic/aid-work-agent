using System;
using System.IO;
using System.Threading;

class RuntimeInstallerTimerFixture {
    static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        if (args.Length > 0) {
            File.WriteAllText(Path.Combine(root, "fixture-requested.txt"), args[0]);
            return 0;
        }
        File.WriteAllText(Path.Combine(root, "fixture-started.txt"), "ready");
        while (!File.Exists(Path.Combine(root, "fixture-release.txt"))) Thread.Sleep(25);
        File.WriteAllText(Path.Combine(root, "fixture-graceful.txt"), "completed");
        return 0;
    }
}
