using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher
{
    private const string Port = "8766";

    [STAThread]
    private static int Main(string[] args)
    {
        bool stop = Array.IndexOf(args, "--stop") >= 0;
        bool quiet = Array.IndexOf(args, "--no-browser") >= 0;
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string data = Environment.GetEnvironmentVariable("RESEARCHDESK_DATA");
        if (String.IsNullOrWhiteSpace(data))
            data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                "ResearchDesk", "Data");
        try
        {
            Directory.CreateDirectory(data);
            string log = Path.Combine(data, "launcher.log");
            using (var mutex = new Mutex(false, @"Local\ResearchDesk.Launcher"))
            {
                try
                {
                    if (!mutex.WaitOne(TimeSpan.FromSeconds(30)))
                        throw new InvalidOperationException("另一个研知启动器正在工作，请稍后重试。");
                }
                catch (AbandonedMutexException) { }
                try
                {
                    string service = Path.Combine(root, "ResearchDesk", "service");
                    var start = new ProcessStartInfo
                    {
                        FileName = Path.Combine(service, ".conda", "python.exe"),
                        Arguments = "-X utf8 -m researchdesk " + (stop ? "stop" : "start --reuse --port " + Port),
                        WorkingDirectory = service,
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true,
                        StandardOutputEncoding = Encoding.UTF8,
                        StandardErrorEncoding = Encoding.UTF8
                    };
                    start.EnvironmentVariables["RESEARCHDESK_DATA"] = Path.GetFullPath(data);
                    using (var process = Process.Start(start))
                    {
                        var output = process.StandardOutput.ReadToEndAsync();
                        var errors = process.StandardError.ReadToEndAsync();
                        process.WaitForExit();
                        File.WriteAllText(log, output.Result + errors.Result, Encoding.UTF8);
                        if (process.ExitCode != 0)
                            throw new InvalidOperationException(errors.Result.Trim() + "\n日志 " + log);
                    }
                    if (!stop && !quiet)
                        Process.Start(new ProcessStartInfo("http://127.0.0.1:" + Port) { UseShellExecute = true });
                    return 0;
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
        catch (Exception error)
        {
            if (!quiet)
                MessageBox.Show(error.Message, "研知", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
