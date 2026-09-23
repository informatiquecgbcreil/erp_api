// Harnais de recette compilé uniquement par la CI, jamais dans le programme livré.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;

namespace MonCentreSocial {
static class SystemSmoke {
    static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
    static void MigrationHelper(string helper, string mode, Dictionary<string, object> source, Dictionary<string, object> target) {
        var info = new ProcessStartInfo(Path.Combine(Program.Install,"python","python.exe"),
            "-B " + Program.Quote(helper) + " " + Program.Quote(Program.Install) + " " + mode) {
            UseShellExecute=false, CreateNoWindow=true, RedirectStandardInput=true
        };
        using (var process = Process.Start(info)) {
            var payload = Program.Utf8.GetBytes(Program.Json.Serialize(new { source=source, target=target }));
            process.StandardInput.BaseStream.Write(payload,0,payload.Length); process.StandardInput.Close();
            if (!process.WaitForExit(600000)) { process.Kill(); throw new Exception("Délai de recette migration dépassé"); }
            Check(process.ExitCode == 0, "Recette migration : " + mode);
        }
    }
    static void Healthy(Dictionary<string, object> config) {
        var request = (HttpWebRequest)WebRequest.Create((string)config["url"] + "/healthz");
        request.Proxy = null; request.Timeout = 30000;
        using (var response = (HttpWebResponse)request.GetResponse()) Check(response.StatusCode == HttpStatusCode.OK, "HTTPS indisponible");
    }
    static void KioskHealthy(Dictionary<string, object> config) {
        var request = (HttpWebRequest)WebRequest.Create((string)config["kiosk_url"] + "/kiosk/");
        request.Proxy = null; request.Timeout = 30000;
        using (var response = (HttpWebResponse)request.GetResponse()) Check(response.StatusCode == HttpStatusCode.OK, "Kiosque mobile indisponible");
        try {
            var blocked = (HttpWebRequest)WebRequest.Create((string)config["kiosk_url"] + "/dashboard"); blocked.Proxy = null; blocked.Timeout = 30000;
            using (var response = (HttpWebResponse)blocked.GetResponse()) Check(false, "Le point d'accès mobile expose l'administration");
        } catch (WebException e) {
            var response = e.Response as HttpWebResponse;
            Check(response != null && response.StatusCode == HttpStatusCode.Forbidden, "Le filtrage du point d'accès mobile est absent");
        }
    }
    static int Main(string[] args) {
        try {
            Check(Environment.GetEnvironmentVariable("GITHUB_ACTIONS") == "true" && Program.IsAdmin,
                  "Cette recette est réservée à une machine CI Windows éphémère et élevée.");
            if (args.Length == 2 && args[0] == "migration") {
                var target = Program.ReadConfiguration();
                var source = new Dictionary<string,object>(target);
                source["data_root"] = Path.Combine(Environment.GetEnvironmentVariable("RUNNER_TEMP"),"ancienne-installation");
                source["db_port"] = Program.FreePort(56432); source["db_name"] = "erp_pedagogie";
                source["db_password"] = Program.Secret(); source["db_admin_password"] = Program.Secret();
                source["admin_email"] = "ancien-compte@example.test"; source["admin_password"] = Program.Secret();
                source["admin_name"] = "Compte conservé";
                try {
                    MigrationHelper(args[1], "prepare", source, target);
                    Program.StopService();
                    target["migration_source"] = source["data_root"]; target["migration_done"] = false;
                    Program.FinishInstallation(target); Healthy(target); KioskHealthy(target);
                    MigrationHelper(args[1], "verify", source, Program.ReadConfiguration());
                } finally { MigrationHelper(args[1], "stop", source, target); }
                return 0;
            }
            if (args.Length == 1 && args[0] == "resume") {
                var stored = Program.ReadConfiguration(); Program.FinishInstallation(stored); Healthy(stored);
                Console.WriteLine("REPRISE_MISE_A_JOUR_OK"); return 0;
            }
            Check(!Directory.Exists(Program.Root) && !Program.ServiceExists(), "Une instance existe déjà : arrêt de la recette.");
            var c = new Dictionary<string,object> {
                {"organization", "Recette Centre Équipe"}, {"admin_name", "Direction"},
                {"admin_email", "recette@example.test"}, {"admin_password", Program.Secret()},
                {"modules", new[] {"presences", "statistiques"}}, {"network", true}, {"hostname", "localhost"},
                {"smtp_host", ""}, {"smtp_port", 587}, {"smtp_user", ""}, {"smtp_password", ""}, {"smtp_sender", ""}
            };
                Program.InstallConfiguration(c, message => Console.WriteLine(message));
            Healthy(c); KioskHealthy(c);
            using (var service = new ServiceController(Program.ServiceName)) Check(service.Status == ServiceControllerStatus.Running, "Service non démarré");
            var report = Path.Combine(Program.Root, "Direction-DSI", "Installation-confidentielle.txt");
            Check(!File.ReadAllText(report).Contains((string)c["secret_key"]) && !File.ReadAllText(report).Contains((string)c["db_password"]), "Un secret figure dans le rapport");
            var security = File.GetAccessControl(report);
            foreach (FileSystemAccessRule rule in security.GetAccessRules(true, true, typeof(SecurityIdentifier))) {
                if (rule.AccessControlType != AccessControlType.Allow) continue;
                var sid = (SecurityIdentifier)rule.IdentityReference;
                Check(sid.IsWellKnown(WellKnownSidType.LocalSystemSid) || sid.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid), "ACL du dossier trop large");
            }
            Check((string)Program.ReadConfiguration()["db_password"] == (string)c["db_password"], "Configuration DPAPI invalide");
            Program.StopService();
            Check(!File.Exists(Path.Combine(Program.Root, "postgresql", "postmaster.pid")), "PostgreSQL encore démarré");
            Program.FinishInstallation(c); Healthy(c); KioskHealthy(c);
            Console.WriteLine("SERVICE_HTTPS_DPAPI_ACL_ARRET_REDEMARRAGE_OK");
            return 0;
        } catch (Exception e) {
            Console.Error.WriteLine(e.GetType().Name + ": " + e.Message);
            return 1;
        }
    }
}
}
