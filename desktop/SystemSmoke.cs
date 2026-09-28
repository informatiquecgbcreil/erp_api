// Harnais de recette compilé uniquement par la CI, jamais dans le programme livré.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.AccessControl;
using System.Security.Cryptography.X509Certificates;
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
        using (var response = (HttpWebResponse)request.GetResponse()) {
            Check(response.StatusCode == HttpStatusCode.OK, "HTTPS indisponible");
            // HTTP/3 (UDP) ne doit plus être annoncé : le pare-feu n'ouvre que TCP.
            Check(string.IsNullOrEmpty(response.Headers["Alt-Svc"]), "HTTP/3 encore annoncé par le proxy");
        }
        // Adresse IP du réseau local : certificat valable et vérifié sans DNS.
        var byIp = (HttpWebRequest)WebRequest.Create(Program.AccessUrl(config) + "/healthz");
        byIp.Proxy = null; byIp.Timeout = 30000;
        using (var response = (HttpWebResponse)byIp.GetResponse()) Check(response.StatusCode == HttpStatusCode.OK, "HTTPS par adresse IP indisponible");
    }
    static bool HostAccepted(Dictionary<string, object> config, string host) {
        var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + config["web_port"] + "/healthz");
        request.Proxy = null; request.Timeout = 30000; request.AllowAutoRedirect = false;
        request.Host = host + ":" + config["https_port"];
        try { using (var response = (HttpWebResponse)request.GetResponse()) return (int)response.StatusCode != 400; }
        catch (WebException e) {
            var response = e.Response as HttpWebResponse;
            if (response == null) throw;
            using (response) return (int)response.StatusCode != 400;
        }
    }
    static X509Certificate2 PemCertificate(string pem) {
        return new X509Certificate2(Convert.FromBase64String(pem.Replace("-----BEGIN CERTIFICATE-----", "").Replace("-----END CERTIFICATE-----", "")));
    }
    /// Nom ajouté après l'installation (ex. gestion.cgb) : ajouté à la main dans
    /// le Caddyfile, il était refusé par l'application (400 « not trusted ») et
    /// hors du périmètre de l'autorité contrainte (« Non sécurisé »).
    static void AccessAddressAdded(Dictionary<string, object> c) {
        var rootFile = Path.Combine(Program.Root, "https", "autorite", "racine.crt");
        var before = File.ReadAllText(rootFile);
        var hosts = Program.ParseHosts("https://Gestion-Recette.test:8443/");
        Check(hosts.Count == 1 && hosts[0] == "gestion-recette.test", "Saisie d'une adresse complète mal lue");
        Check(!HostAccepted(c, "gestion-recette.test"), "Nom non déclaré accepté par l'application");
        Check(Program.ChangeAccessAddresses(hosts, delegate { return false; }) == null, "Nouveau certificat créé sans accord");
        Check(File.ReadAllText(rootFile) == before && Program.ExtraHosts(Program.ReadConfiguration()).Count == 0, "Changement appliqué malgré le refus");
        var message = Program.ChangeAccessAddresses(hosts, delegate(string missing) { Check(missing == "gestion-recette.test", "Nom hors autorité mal signalé : " + missing); return true; });
        c["hotes_supplementaires"] = hosts.ToArray();
        Check(message != null && message.Contains("NOUVEAU CERTIFICAT"), "Redéploiement du certificat non signalé");
        var after = File.ReadAllText(rootFile);
        Check(after != before, "Autorité non renouvelée");
        var stored = Program.ReadConfiguration();
        Check(!stored.ContainsKey("renouveler_autorite") && Program.ExtraHosts(stored).Contains("gestion-recette.test"), "Configuration des adresses incorrecte");
        Check(File.ReadAllText(Path.Combine(Program.Root, "https", "Caddyfile")).Contains("https://gestion-recette.test:" + c["https_port"]), "Caddyfile sans le nouveau nom");
        var oldRoot = PemCertificate(before); var newRoot = PemCertificate(after);
        using (var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine)) {
            store.Open(OpenFlags.ReadOnly);
            Check(store.Certificates.Find(X509FindType.FindByThumbprint, oldRoot.Thumbprint, false).Count == 0, "Ancienne autorité encore reconnue par le serveur");
            Check(store.Certificates.Find(X509FindType.FindByThumbprint, newRoot.Thumbprint, false).Count == 1, "Nouvelle autorité absente du magasin");
        }
        Check(new X509Certificate2(Path.Combine(Program.Root, "public", "Certificat-du-centre.cer")).Thumbprint == newRoot.Thumbprint, "Certificat public non mis à jour");
        Check(Directory.GetFiles(Path.Combine(Program.Root, "https", "autorite", "remplacees"), "racine.crt", SearchOption.AllDirectories).Length == 1, "Ancienne autorité non conservée");
        // Chaîne recréée par Caddy sous la nouvelle autorité, vérifiée par Windows.
        Healthy(c);
        Check(HostAccepted(c, "gestion-recette.test") && !HostAccepted(c, "intrus.test"), "Hôtes de confiance de l'application incorrects");
        // Sans nouveau nom : aucune question, aucun nouveau certificat.
        Check(Program.ChangeAccessAddresses(hosts, delegate { throw new Exception("Accord demandé sans raison"); }) != null, "Réenregistrement refusé");
        Check(File.ReadAllText(rootFile) == after, "Autorité renouvelée sans nouveau nom");
    }
    static bool WebCanRead(string path) {
        var webSid = (SecurityIdentifier)new NTAccount("NT SERVICE", Program.ServiceName).Translate(typeof(SecurityIdentifier));
        foreach (FileSystemAccessRule rule in File.GetAccessControl(path).GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (rule.AccessControlType == AccessControlType.Allow && rule.IdentityReference.Equals(webSid)) return true;
        return false;
    }
    static string Capture(string exe, string arguments) {
        using (var p = Process.Start(new ProcessStartInfo(exe, arguments) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true })) {
            string text = p.StandardOutput.ReadToEnd(); p.StandardError.ReadToEnd(); p.WaitForExit(); return p.ExitCode == 0 ? text : "";
        }
    }
    /// Page servie sous le nom public : chaîne vérifiée par Windows (aucune
    /// exception de certificat), rend l'empreinte du certificat présenté.
    static string ServedByName(Dictionary<string, object> config, string name) {
        var request = (HttpWebRequest)WebRequest.Create("https://" + name + ":" + config["https_port"] + "/healthz");
        request.Proxy = null; request.Timeout = 30000; request.KeepAlive = false;
        using (var response = (HttpWebResponse)request.GetResponse()) {
            Check(response.StatusCode == HttpStatusCode.OK, "HTTPS par le nom public indisponible");
            return new X509Certificate2(request.ServicePoint.Certificate).Thumbprint;
        }
    }
    static string LeafThumbprint(string pemFile) {
        var pem = File.ReadAllText(pemFile);
        int start = pem.IndexOf("-----BEGIN CERTIFICATE-----"), end = pem.IndexOf("-----END CERTIFICATE-----");
        return PemCertificate(pem.Substring(start, end - start)).Thumbprint;
    }
    /// Certificat reconnu de bout en bout : Pebble (Let's Encrypt de test),
    /// DNS de test et faux cPanel lancés par desktop/recette_certificat.py.
    static void PublicCertificateRecette(string benchFile) {
        var bench = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(benchFile, Program.Utf8));
        string name = (string)bench["nom"], cpanel = (string)bench["cpanel"], user = (string)bench["utilisateur"], token = (string)bench["jeton"];
        Environment.SetEnvironmentVariable("MCS_ACME_REPERTOIRE", (string)bench["acme"]);
        Environment.SetEnvironmentVariable("MCS_ACME_CA", (string)bench["acme_ca"]);
        Environment.SetEnvironmentVariable("MCS_CPANEL_CA", (string)bench["cpanel_ca"]);
        Environment.SetEnvironmentVariable("MCS_DNS_SERVEURS", (string)bench["dns"]);
        var hosts = Path.Combine(Environment.SystemDirectory, "drivers", "etc", "hosts");
        var hostsBefore = File.ReadAllText(hosts);
        var roots = new List<X509Certificate2>();
        var pems = File.ReadAllText((string)bench["racine"]).Split(new[] { "-----END CERTIFICATE-----" }, StringSplitOptions.RemoveEmptyEntries);
        foreach (var pem in pems) if (pem.Contains("BEGIN CERTIFICATE")) roots.Add(PemCertificate(pem.Substring(pem.IndexOf("-----BEGIN CERTIFICATE-----"))));
        Check(roots.Count > 0, "Racine Pebble introuvable");
        try {
            File.AppendAllText(hosts, Environment.NewLine + "127.0.0.1 " + name + " # recette Mon Centre Social" + Environment.NewLine);
            using (var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine)) { store.Open(OpenFlags.ReadWrite); foreach (var r in roots) store.Add(r); }
            var certificate = Path.Combine(Program.Root, "https", "public", "certificat.crt");
            var key = Path.Combine(Program.Root, "https", "public", "certificat.key");
            // 1. Jeton refusé : message clair, rien de changé.
            string refusal = "";
            try { Program.ConfigurePublicCertificate(name, cpanel, user, "mauvais-jeton"); } catch (Exception e) { refusal = e.Message; }
            Check(refusal.Contains("rien n'a été modifié") && refusal.Contains("jeton"), "Jeton refusé mal signalé : " + refusal);
            Check(Program.PublicName(Program.ReadConfiguration()).Length == 0 && !File.Exists(certificate), "Changement malgré l'échec");
            Check(Capture(Program.SystemExe("schtasks.exe"), "/Query /TN " + Program.Quote(Program.RenewalTaskName)).Length == 0, "Tâche créée malgré l'échec");
            // 2. Premier certificat.
            var message = Program.ConfigurePublicCertificate("https://" + name.ToUpperInvariant() + "/", cpanel, user, token);
            var c = Program.ReadConfiguration();
            Check(message.Contains("https://" + name + ":" + c["https_port"]), "Adresse publique non annoncée : " + message);
            Check(Program.PublicName(c) == name && Convert.ToString(c["certificat_public_jeton"]) == token, "Configuration du nom public incorrecte");
            var serviceJson = Program.Utf8.GetString(System.Security.Cryptography.ProtectedData.Unprotect(File.ReadAllBytes(Program.ServiceConfigFile), null, System.Security.Cryptography.DataProtectionScope.LocalMachine));
            Check(!serviceJson.Contains(token) && serviceJson.Contains(name), "Le jeton cPanel est lisible par le service web");
            var task = Capture(Program.SystemExe("schtasks.exe"), "/Query /TN " + Program.Quote(Program.RenewalTaskName) + " /XML");
            Check(task.Contains("--certificat-renouveler") && task.Contains("S-1-5-18"), "Tâche de renouvellement SYSTEM absente");
            Check(!WebCanRead(key) && !WebCanRead(Path.Combine(Program.Root, "private", "acme", "compte.pem")), "Le service web peut lire une clé du certificat reconnu");
            // Fins de ligne Windows (\r\n) : Python écrit le Caddyfile en mode texte.
            var caddyfile = File.ReadAllText(Path.Combine(Program.Root, "https", "Caddyfile")).Replace("\r\n", "\n");
            Check(caddyfile.Contains("https://" + name + ":" + c["https_port"] + " {\n tls "), "Caddyfile sans le certificat reconnu :\n" + caddyfile);
            // 3. Nom public servi avec la chaîne Let's Encrypt (de test), vérifiée par Windows.
            string served = ServedByName(c, name);
            Check(served == LeafThumbprint(certificate), "Le proxy ne présente pas le certificat reconnu");
            Check(HostAccepted(c, name), "Nom public refusé par l'application");
            Healthy(c);
            // 4. Tâche quotidienne : rien à faire tant que le certificat est loin de l'échéance.
            var bytes = File.ReadAllBytes(certificate);
            Check(Program.RenewPublicCertificate() == 0 && Convert.ToBase64String(File.ReadAllBytes(certificate)) == Convert.ToBase64String(bytes), "Renouvellement inutile effectué");
            // 5. Certificat perdu : la tâche en obtient un autre et relance le proxy.
            File.Delete(certificate);
            Check(Program.RenewPublicCertificate() == 0 && File.Exists(certificate), "Certificat non renouvelé");
            Check(ServedByName(c, name) == LeafThumbprint(certificate) && LeafThumbprint(certificate) != served, "Nouveau certificat non servi après renouvellement");
            Check(File.ReadAllText(Path.Combine(Program.Root, "logs", "certificat.log")).Contains("Renouvellement " + name), "Journal du certificat absent");
            // 6. Désactivation : retour à l'adresse du serveur, tâche et jeton retirés.
            Program.DisablePublicCertificate();
            c = Program.ReadConfiguration();
            Check(Program.PublicName(c).Length == 0 && !c.ContainsKey("certificat_public_jeton"), "Nom public encore configuré");
            Check(Capture(Program.SystemExe("schtasks.exe"), "/Query /TN " + Program.Quote(Program.RenewalTaskName)).Length == 0, "Tâche de renouvellement restée");
            Check(!File.ReadAllText(Path.Combine(Program.Root, "https", "Caddyfile")).Contains(name), "Nom public resté dans le Caddyfile");
            Healthy(c);
        } finally {
            File.WriteAllText(hosts, hostsBefore);
            using (var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine)) { store.Open(OpenFlags.ReadWrite); foreach (var r in roots) store.Remove(r); }
        }
    }
    /// Audit 6.2 : un port occupé sur une seule adresse (VPN, Tailscale) doit
    /// être vu comme occupé.
    static void PortsReallyFree() {
        int port = Program.FreePort(47000);
        var other = new System.Net.Sockets.TcpListener(IPAddress.Any, port);
        other.Start();
        try {
            Check(Program.PortInUse(port), "Port occupé sur toutes les adresses non détecté");
            Check(Program.FreePort(port) != port, "FreePort propose un port déjà occupé");
        } finally { other.Stop(); }
        Console.WriteLine("PORTS_OCCUPES_DETECTES_OK");
    }
    /// Audit 6.5 : sauvegarde quotidienne faite au démarrage, vérifiée ; puis
    /// restauration complète du lot dans une base jetable.
    static void BackupAndRestore() {
        var state = Path.Combine(Program.Root, "backups", "etat-sauvegarde-quotidienne.json");
        for (int i = 0; ; i++) {
            if (File.Exists(state)) {
                var etat = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(state, Program.Utf8));
                if (etat.ContainsKey("lot_ok") && Convert.ToBoolean(etat["lot_ok"])) break;
            }
            Check(i < 600, "La sauvegarde quotidienne vérifiée n'a pas été produite");
            System.Threading.Thread.Sleep(1000);
        }
        // Registres hors base : le service (son propre compte, pas celui de la
        // recette) a pu poser les verrous dans runtime et joindre au lot une
        // copie valide des registres.
        var etatLot = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(state, Program.Utf8));
        bool copieRegistres = false;
        foreach (var element in (System.Collections.IEnumerable)etatLot["controles"]) {
            var controle = element as Dictionary<string, object>;
            if (controle != null && (string)controle["nom"] == "Copie des registres" && controle["ok"] is bool && (bool)controle["ok"]) copieRegistres = true;
        }
        Check(copieRegistres, "La sauvegarde du service n'emporte pas de copie valide des registres");
        Check(File.Exists(Path.Combine(Program.Root, "backups", (string)etatLot["base"] + "_registres.json")), "Copie des registres absente du lot");
        foreach (var verrou in new[] { "numeros-emis.json.lock", "registre-effacements.json.lock" })
            Check(File.Exists(Path.Combine(Program.Root, "runtime", verrou)), "Verrou de registre non créé par le service : " + verrou);
        Console.WriteLine("REGISTRES_COPIES_DANS_LE_LOT_PAR_LE_SERVICE_OK");
        int code = Program.RunPython("--restore-test", Program.ReadConfiguration(), 1800000);
        var report = Path.Combine(Program.Root, "private", "essai-restauration.json");
        Check(code == 0 && File.Exists(report), "La restauration complète du dernier lot a échoué");
        Console.WriteLine("SAUVEGARDE_VERIFIEE_ET_RESTAURATION_COMPLETE_OK " + File.ReadAllText(report, Program.Utf8));
    }
    static void KioskHealthy(Dictionary<string, object> config) {
        var request = (HttpWebRequest)WebRequest.Create((string)config["kiosk_url"] + "/kiosk/");
        request.Proxy = null; request.Timeout = 30000;
        using (var response = (HttpWebResponse)request.GetResponse()) Check(response.StatusCode == HttpStatusCode.OK, "Kiosque mobile indisponible");
        var sources = (HttpWebRequest)WebRequest.Create((string)config["kiosk_url"] + "/sources");
        sources.Proxy = null; sources.Timeout = 30000;
        using (var response = (HttpWebResponse)sources.GetResponse())
            Check(response.StatusCode == HttpStatusCode.OK && response.ContentLength == new FileInfo(Path.Combine(Program.Install,"sources-Mon-Centre-Social.zip")).Length,
                  "Les sources de la version installée sont indisponibles");
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
                source["db_password"] = Program.Secret(); source["db_admin_password"] = Program.Secret() + "!";
                source["admin_email"] = "ancien-compte@example.test"; source["admin_password"] = Program.Secret();
                source["admin_name"] = "Compte conservé";
                // initdb retire les droits Administrateurs de son jeton. La source
                // éphémère doit donc accorder ses droits au compte CI lui-même.
                var sourceRoot = (string)source["data_root"];
                Directory.CreateDirectory(sourceRoot);
                var sourceAcl = new DirectorySecurity();
                sourceAcl.SetAccessRuleProtection(true,false);
                sourceAcl.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,
                    FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None, AccessControlType.Allow));
                Directory.SetAccessControl(sourceRoot,sourceAcl);
                try {
                    MigrationHelper(args[1], "prepare", source, target);
                    Program.StopService();
                    // Après l'installation neuve 18, vérifie aussi un cluster 17
                    // encore actif, puis la reprise d'une tentative 17 inachevée.
                    Directory.Move(Path.Combine(Program.Root,"postgresql"),Path.Combine(Program.Root,"runtime","recette-installation-vierge"));
                    Program.SecureDirectory(Path.Combine(Program.Root,"postgresql"),true,true);
                    File.Delete(Path.Combine(Program.Root,"runtime","provisioned"));
                    target["db_major"] = 17; target["admin_password"] = Program.Secret();
                    Program.FinishInstallation(target); Healthy(target); Program.StopService();
                    Check(File.ReadAllText(Path.Combine(Program.Root,"postgresql","PG_VERSION")).Trim() == "17", "Le cluster 17 existant a changé de moteur");
                    target["migration_source"] = source["data_root"]; target["migration_done"] = false;
                    Program.FinishInstallation(target); Healthy(target); KioskHealthy(target);
                    Check(File.ReadAllText(Path.Combine(Program.Root,"postgresql","PG_VERSION")).Trim() == "18", "La reprise n'utilise pas PostgreSQL 18");
                    var retained = Directory.GetDirectories(Path.Combine(Program.Root,"runtime"),"reprise-pg17-*");
                    Check(retained.Length == 1 && File.ReadAllText(Path.Combine(retained[0],"PG_VERSION")).Trim() == "17", "Le cluster de la tentative précédente n'a pas été conservé");
                    MigrationHelper(args[1], "verify", source, Program.ReadConfiguration());
                    Console.WriteLine("REPRISE_APRES_TENTATIVE_POSTGRESQL17_CONSERVEE_OK");
                } finally { MigrationHelper(args[1], "stop", source, target); }
                return 0;
            }
            if (args.Length == 2 && args[0] == "certificat") {
                PublicCertificateRecette(args[1]);
                Console.WriteLine("CERTIFICAT_RECONNU_OK"); return 0;
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
            PortsReallyFree();
                Program.InstallConfiguration(c, message => Console.WriteLine(message));
            Healthy(c); KioskHealthy(c);
            BackupAndRestore();
            AccessAddressAdded(c);
            var rootAfterRenewal = File.ReadAllText(Path.Combine(Program.Root,"https","autorite","racine.crt"));
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
            var serviceJson = Program.Utf8.GetString(System.Security.Cryptography.ProtectedData.Unprotect(File.ReadAllBytes(Program.ServiceConfigFile),null,System.Security.Cryptography.DataProtectionScope.LocalMachine));
            Check(!serviceJson.Contains("db_admin_password") && !serviceJson.Contains("admin_password"),"Un secret de provisionnement reste accessible au service web");
            var webSid = (SecurityIdentifier)new NTAccount("NT SERVICE",Program.ServiceName).Translate(typeof(SecurityIdentifier));
            // Installation neuve : autorité contrainte aux noms et adresses du centre (audit 6.7).
            Check(File.Exists(Path.Combine(Program.Root,"https","autorite","racine.crt")), "L'autorité contrainte n'a pas été créée");
            foreach (var protectedFile in new[] {Program.ConfigFile,Program.ProxyRootKey()}) {
                foreach (FileSystemAccessRule rule in File.GetAccessControl(protectedFile).GetAccessRules(true,true,typeof(SecurityIdentifier)))
                    Check(rule.AccessControlType != AccessControlType.Allow || !rule.IdentityReference.Equals(webSid),"Le web peut lire la configuration administrative ou la clé CA");
            }
            var privileges = (string[])Microsoft.Win32.Registry.GetValue("HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\" + Program.ServiceName,"RequiredPrivileges",new string[0]);
            Check(Array.IndexOf(privileges,"SeImpersonatePrivilege") < 0,"Le service conserve SeImpersonate");
            Program.StopService();
            Check(!File.Exists(Path.Combine(Program.Root, "postgresql", "postmaster.pid")), "PostgreSQL encore démarré");
            Program.FinishInstallation(c); Healthy(c); KioskHealthy(c);
            Check(File.ReadAllText(Path.Combine(Program.Root,"https","autorite","racine.crt")) == rootAfterRenewal, "Autorité changée au redémarrage");
            Check(HostAccepted(c, "gestion-recette.test"), "Nom d'accès perdu au redémarrage");
            Console.WriteLine("SERVICE_HTTPS_DPAPI_ACL_ARRET_REDEMARRAGE_ADRESSES_OK");
            return 0;
        } catch (Exception e) {
            Console.Error.WriteLine(e.GetType().Name + ": " + e.Message);
            return 1;
        }
    }
}
}
