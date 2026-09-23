// Harnais de recette compilé uniquement par la CI, jamais dans le programme livré.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;

namespace MonCentreSocial {
static class SystemSmoke {
    static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
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
