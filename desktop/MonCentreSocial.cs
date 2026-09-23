// Windows 10 1809+ / Windows Server 2019+, .NET Framework fourni par Windows.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Mail;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Runtime.InteropServices;

namespace MonCentreSocial {
static class Program {
    internal const string ServiceName = "MonCentreSocial";
    internal static readonly string Install = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    internal static readonly string Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "MonCentreSocial");
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    internal static readonly Encoding Utf8 = new UTF8Encoding(false);
    internal static string Exe { get { return Path.Combine(Install, "MonCentreSocial.exe"); } }
    internal static string ConfigFile { get { return Path.Combine(Root, "private", "configuration.dpapi"); } }
    internal static bool IsAdmin { get { return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); } }
    internal static Icon Logo { get { return new Icon(Path.Combine(Install, "mon-centre-social.ico")); } }

    [STAThread]
    static int Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string mode = args.Length == 0 ? "--tray" : args[0];
        try {
            if (mode == "--service") { ServiceBase.Run(new CentreService()); return 0; }
            if (mode == "--self-test") {
                // Vérifie le chiffrement et la validation sans toucher l'installation.
                byte[] value = Utf8.GetBytes("test local sans secret");
                if (Utf8.GetString(ProtectedData.Unprotect(ProtectedData.Protect(value, null, DataProtectionScope.LocalMachine), null, DataProtectionScope.LocalMachine)) != "test local sans secret") return 2;
                if (Quote("un chemin avec espaces") != "\"un chemin avec espaces\"") return 3;
                return 0;
            }
            if (mode == "--preview" && args.Length == 2) {
                using (var form = new SetupWizard()) {
                    form.Opacity = 0; form.Show(); form.Update();
                    using (var bmp = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(bmp, new Rectangle(0, 0, bmp.Width, bmp.Height)); bmp.Save(args[1]); }
                }
                return 0;
            }
            if (mode == "--tray") { bool created; using (var mutex = new Mutex(true, "Local\\MonCentreSocialTray", out created)) { if (created) Application.Run(new Tray()); else Open(); } return 0; }
            if (!IsAdmin) { var p = Process.Start(new ProcessStartInfo(Exe, mode) { UseShellExecute = true, Verb = "runas" }); p.WaitForExit(); return p.ExitCode; }
            if (mode == "--configure") {
                if (File.Exists(ConfigFile)) { FinishInstallation(ReadConfiguration()); return 0; }
                using (var choice = new InstallationChoice()) {
                    if (choice.ShowDialog() != DialogResult.OK) return 1;
                    using (var wizard = new SetupWizard(choice.Source, choice.Connection, choice.LegacyService))
                        return wizard.ShowDialog() == DialogResult.OK ? 0 : 1;
                }
            }
            if (mode == "--restart") { StopService(); StartService(); return 0; }
            if (mode == "--stop") { StopService(); return 0; }
            if (mode == "--uninstall") {
                StopService();
                if (ServiceExists()) Run(SystemExe("sc.exe"), "delete " + ServiceName, false);
                Run(SystemExe("netsh.exe"), "advfirewall firewall delete rule name=\"Mon Centre Social HTTPS\"", false);
                Run(SystemExe("netsh.exe"), "advfirewall firewall delete rule name=\"Mon Centre Social Kiosque mobile\"", false);
                return 0; // Les données et le dossier de direction restent conservés.
            }
            return 2;
        } catch (Exception ex) {
            // Les messages ne comprennent jamais la configuration ni les arguments secrets.
            MessageBox.Show("L'opération n'a pas abouti.\n\n" + ex.Message + "\n\nVous pouvez relancer « Configurer Mon Centre Social » depuis le menu Démarrer.", "Mon Centre Social", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static string SystemExe(string name) { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), name); }
    internal static string Quote(string s) {
        var b = new StringBuilder("\""); int slashes = 0;
        foreach (char c in s) {
            if (c == '\\') { slashes++; continue; }
            b.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); slashes = 0; b.Append(c);
        }
        b.Append('\\', slashes * 2); b.Append('"'); return b.ToString();
    }
    internal static int Run(string exe, string args, bool check = true) {
        using (var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, RedirectStandardOutput = true, RedirectStandardError = true })) {
            p.OutputDataReceived += delegate {}; p.ErrorDataReceived += delegate {}; p.BeginOutputReadLine(); p.BeginErrorReadLine();
            if (!p.WaitForExit(120000)) { p.Kill(); throw new Exception("Une opération Windows a dépassé son délai."); }
            if (check && p.ExitCode != 0) throw new Exception(Path.GetFileName(exe) + " a signalé une erreur (" + p.ExitCode + ").");
            return p.ExitCode;
        }
    }
    internal static bool ServiceExists() { foreach (var s in ServiceController.GetServices()) using (s) if (s.ServiceName == ServiceName) return true; return false; }
    internal static void StopService() {
        if (!ServiceExists()) return;
        using (var s = new ServiceController(ServiceName)) {
            if (s.Status != ServiceControllerStatus.Stopped) { if (s.Status != ServiceControllerStatus.StopPending) s.Stop(); s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(100)); }
        }
    }
    internal static void StartService() {
        using (var s = new ServiceController(ServiceName)) {
            if (s.Status == ServiceControllerStatus.Stopped) s.Start();
            s.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(40));
        }
    }
    internal static void RegisterService() {
        if (!ServiceExists()) Run(SystemExe("sc.exe"), "create " + ServiceName + " binPath= " + Quote(Quote(Exe) + " --service") + " start= auto obj= " + Quote("NT SERVICE\\" + ServiceName) + " DisplayName= \"Mon Centre Social\"");
        Run(SystemExe("sc.exe"), "description " + ServiceName + " \"Mon Centre Social : application, base locale et sauvegarde quotidienne.\"");
        Run(SystemExe("sc.exe"), "failure " + ServiceName + " reset= 86400 actions= restart/10000/restart/30000/restart/60000");
        Run(SystemExe("sc.exe"), "sidtype " + ServiceName + " unrestricted");
    }
    internal static string Secret() { var bytes = new byte[32]; using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes); return Convert.ToBase64String(bytes); }
    internal static void GuardPath(string path) {
        for (var info = new DirectoryInfo(Path.GetFullPath(path)); info != null; info = info.Parent)
            if (info.Exists && (info.Attributes & FileAttributes.ReparsePoint) != 0) throw new Exception("Un lien de dossier n'est pas autorisé dans le stockage de l'application.");
        if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new Exception("Un lien de fichier n'est pas autorisé.");
    }
    internal static void SecureDirectory(string path, bool service, bool writable, bool publicRead = false) {
        GuardPath(path); Directory.CreateDirectory(path);
        var security = new DirectorySecurity(); security.SetAccessRuleProtection(true, false);
        var inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
        foreach (var sid in new[] { WellKnownSidType.BuiltinAdministratorsSid, WellKnownSidType.LocalSystemSid })
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null), FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
        if (service) security.AddAccessRule(new FileSystemAccessRule(new NTAccount("NT SERVICE", ServiceName), writable ? FileSystemRights.Modify : FileSystemRights.ReadAndExecute, inherit, PropagationFlags.None, AccessControlType.Allow));
        if (publicRead) security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null), FileSystemRights.ReadAndExecute, inherit, PropagationFlags.None, AccessControlType.Allow));
        security.SetOwner(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null));
        Directory.SetAccessControl(path, security);
    }
    internal static Dictionary<string, object> ReadConfiguration() {
        return Json.Deserialize<Dictionary<string, object>>(Utf8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(ConfigFile), null, DataProtectionScope.LocalMachine)));
    }
    internal static void SaveConfiguration(Dictionary<string, object> c) {
        GuardPath(ConfigFile);
        byte[] encrypted = ProtectedData.Protect(Utf8.GetBytes(Json.Serialize(c)), null, DataProtectionScope.LocalMachine);
        var temporary = ConfigFile + ".new"; GuardPath(temporary); File.WriteAllBytes(temporary, encrypted);
        if (File.Exists(ConfigFile)) File.Replace(temporary, ConfigFile, null); else File.Move(temporary, ConfigFile);
    }
    internal static int FreePort(int start) {
        for (int port = start; port < start + 100; port++) try { var l = new TcpListener(IPAddress.Loopback, port); l.Start(); l.Stop(); return port; } catch (SocketException) {}
        throw new Exception("Aucun port disponible pour l'application.");
    }
    static bool IsUsableLanAddress(IPAddress address) {
        if (address == null || address.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(address)) return false;
        var bytes = address.GetAddressBytes();
        return bytes.Length == 4 && !(bytes[0] == 169 && bytes[1] == 254);
    }
    internal static string LanAddress() {
        string fallback = null;
        try {
            foreach (var adapter in NetworkInterface.GetAllNetworkInterfaces()) {
                if (adapter.OperationalStatus != OperationalStatus.Up) continue;
                bool preferred = adapter.NetworkInterfaceType == NetworkInterfaceType.Ethernet || adapter.NetworkInterfaceType == NetworkInterfaceType.Wireless80211;
                foreach (var item in adapter.GetIPProperties().UnicastAddresses) {
                    if (!IsUsableLanAddress(item.Address)) continue;
                    if (preferred) return item.Address.ToString();
                    if (fallback == null) fallback = item.Address.ToString();
                }
            }
        } catch (NetworkInformationException) { }
        if (fallback != null) return fallback;
        try {
            foreach (var address in Dns.GetHostEntry(Dns.GetHostName()).AddressList)
                if (IsUsableLanAddress(address)) return address.ToString();
        } catch (SocketException) { }
        throw new Exception("Aucune adresse IPv4 de réseau local n'a été trouvée. Connectez le serveur au réseau puis relancez l'assistant.");
    }
    internal static void EnsureNetworkSettings(Dictionary<string, object> c) {
        bool network = c.ContainsKey("network") && Convert.ToBoolean(c["network"]);
        if (!network) {
            if (!c.ContainsKey("lan_ip") || string.IsNullOrWhiteSpace(Convert.ToString(c["lan_ip"]))) c["lan_ip"] = "127.0.0.1";
            if (!c.ContainsKey("kiosk_url") || string.IsNullOrWhiteSpace(Convert.ToString(c["kiosk_url"]))) c["kiosk_url"] = c["url"];
            return;
        }
        string detectedLanIp = LanAddress();
        bool lanAddressChanged = !c.ContainsKey("lan_ip") || !string.Equals(Convert.ToString(c["lan_ip"]), detectedLanIp, StringComparison.OrdinalIgnoreCase);
        c["lan_ip"] = detectedLanIp;
        if (!c.ContainsKey("kiosk_http_port") || Convert.ToInt32(c["kiosk_http_port"]) < 1) c["kiosk_http_port"] = FreePort(8080);
        if (lanAddressChanged || !c.ContainsKey("kiosk_url") || string.IsNullOrWhiteSpace(Convert.ToString(c["kiosk_url"]))) c["kiosk_url"] = "http://" + c["lan_ip"] + ":" + c["kiosk_http_port"];
    }
    internal static void InstallConfiguration(Dictionary<string, object> c, Action<string> progress) {
        if (File.Exists(ConfigFile)) throw new Exception("Une configuration existe déjà. Relancez l'assistant pour la reprendre.");
        progress("Préparation du service et protection des dossiers…");
        GuardPath(Root); RegisterService();
        SecureDirectory(Root, true, false);
        foreach (var name in new[] { "instance", "uploads", "logs", "backups", "runtime", "postgresql" }) SecureDirectory(Path.Combine(Root, name), true, true);
        SecureDirectory(Path.Combine(Root, "private"), true, false);
        SecureDirectory(Path.Combine(Root, "Direction-DSI"), false, false);
        SecureDirectory(Path.Combine(Root, "public"), false, false, true);
        c["data_root"] = Root; c["db_password"] = Secret(); c["db_admin_password"] = Secret(); c["secret_key"] = Secret();
        c["db_port"] = FreePort(55432); c["web_port"] = FreePort(18080); c["https_port"] = FreePort(8443);
        c["url"] = (bool)c["network"] ? "https://" + c["hostname"] + ":" + c["https_port"] : "http://127.0.0.1:" + c["web_port"];
        if ((bool)c["network"]) { c["lan_ip"] = LanAddress(); c["kiosk_http_port"] = FreePort(8080); }
        EnsureNetworkSettings(c);
        SaveConfiguration(c);
        WriteReport(c); // Produit avant le démarrage, reste disponible en cas de reprise.
        var publicFile = Path.Combine(Root, "public", "url.txt"); GuardPath(publicFile); File.WriteAllText(publicFile, (string)c["url"], Utf8);
        var kioskFile = Path.Combine(Root, "public", "kiosk-url.txt"); GuardPath(kioskFile); File.WriteAllText(kioskFile, (string)c["kiosk_url"] + "/kiosk/", Utf8);
        progress("Initialisation de la base et des outils du centre… Cela peut prendre quelques minutes.");
        FinishInstallation(c);
    }
    internal static void FinishInstallation(Dictionary<string, object> c) {
        bool activating = c.ContainsKey("migration_source") && !string.IsNullOrEmpty(Convert.ToString(c["migration_source"]))
            && (!c.ContainsKey("migration_done") || !Convert.ToBoolean(c["migration_done"])
                || (c.ContainsKey("migration_pending_activation") && Convert.ToBoolean(c["migration_pending_activation"])));
        try {
        if (activating) {
            StopService();
            File.WriteAllText(Path.Combine(Root,"private","activation.pending"),"1",Utf8);
        }
        if (c.ContainsKey("migration_source") && !string.IsNullOrEmpty(Convert.ToString(c["migration_source"]))
            && (!c.ContainsKey("migration_done") || !Convert.ToBoolean(c["migration_done"]))) RunMigration(c);
        // Reprise après une interruption entre l'enregistrement et la création du dossier.
        EnsureNetworkSettings(c);
        SaveConfiguration(c);
        var reportPath = Path.Combine(Root, "Direction-DSI", "Installation-confidentielle.txt");
        if (!File.Exists(reportPath) || !File.ReadAllText(reportPath, Utf8).Contains("Adresse kiosque")) WriteReport(c);
        var publicFile = Path.Combine(Root, "public", "url.txt"); GuardPath(publicFile);
        File.WriteAllText(publicFile, (string)c["url"], Utf8);
        var kioskFile = Path.Combine(Root, "public", "kiosk-url.txt"); GuardPath(kioskFile); File.WriteAllText(kioskFile, (string)c["kiosk_url"] + "/kiosk/", Utf8);
        RegisterService(); StartService();
        var ready = Path.Combine(Root, "runtime", "ready");
        for (int i = 0; i < 480; i++) { if (File.Exists(ready)) break; Thread.Sleep(500); if (i == 479) throw new Exception("Le service n'est pas prêt. Le diagnostic est dans " + Path.Combine(Root, "logs") + "."); }
        if ((bool)c["network"]) {
            var certPath = Path.Combine(Root, "runtime", "tls", "pki", "authorities", "local", "root.crt");
            var pem = File.ReadAllText(certPath).Replace("-----BEGIN CERTIFICATE-----", "").Replace("-----END CERTIFICATE-----", "");
            var certificate = new X509Certificate2(Convert.FromBase64String(pem));
            using (var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine)) { store.Open(OpenFlags.ReadWrite); store.Add(certificate); }
            var publicCertificate = Path.Combine(Root, "public", "Certificat-du-centre.cer"); GuardPath(publicCertificate);
            File.WriteAllBytes(publicCertificate, certificate.Export(X509ContentType.Cert));
            Run(SystemExe("netsh.exe"), "advfirewall firewall delete rule name=\"Mon Centre Social HTTPS\"", false);
            Run(SystemExe("netsh.exe"), "advfirewall firewall add rule name=\"Mon Centre Social HTTPS\" dir=in action=allow protocol=TCP localport=" + c["https_port"] + " remoteip=LocalSubnet profile=private,domain program=" + Quote(Path.Combine(Install, "caddy", "caddy.exe")));
            Run(SystemExe("netsh.exe"), "advfirewall firewall delete rule name=\"Mon Centre Social Kiosque mobile\"", false);
            Run(SystemExe("netsh.exe"), "advfirewall firewall add rule name=\"Mon Centre Social Kiosque mobile\" dir=in action=allow protocol=TCP localport=" + c["kiosk_http_port"] + " remoteip=LocalSubnet profile=private,domain program=" + Quote(Path.Combine(Install, "caddy", "caddy.exe")));
        }
        if (activating) {
            var oldName = c.ContainsKey("migration_service") ? Convert.ToString(c["migration_service"]) : "";
            if (oldName.Length > 0) Run(SystemExe("sc.exe"), "config " + Quote(oldName) + " start= disabled");
            c.Remove("migration_pending_activation"); c.Remove("migration_restart_old");
        }
        c.Remove("admin_password"); c.Remove("migration_uri"); SaveConfiguration(c); WriteReport(c);
        File.Delete(Path.Combine(Root,"private","activation.pending"));
        } catch {
            if (activating) {
                // Aucun retour automatique une fois le centre ouvert aux utilisateurs.
                // Ici, l'activation n'est pas terminée : on ferme la cible avant de
                // reprendre l'ancienne instance et on exigera une nouvelle copie.
                StopService();
                c["migration_done"] = false; c.Remove("migration_pending_activation");
                File.Delete(Path.Combine(Root,"runtime","reprise","complete.json"));
                SaveConfiguration(c);
                var oldName = c.ContainsKey("migration_service") ? Convert.ToString(c["migration_service"]) : "";
                if (oldName.Length > 0 && c.ContainsKey("migration_restart_old") && Convert.ToBoolean(c["migration_restart_old"]))
                    using (var old = new ServiceController(oldName)) { if (old.Status == ServiceControllerStatus.Stopped) old.Start(); }
            }
            throw;
        }
    }
    internal static void RunMigration(Dictionary<string, object> c) {
        string oldName = c.ContainsKey("migration_service") ? Convert.ToString(c["migration_service"]) : "";
        if (oldName == ServiceName || (oldName.Length > 0 && !Regex.IsMatch(oldName, "^[A-Za-z0-9_. -]{1,150}$")))
            throw new Exception("Nom d'ancien service invalide.");
        bool wasRunning = false;
        try {
            if (oldName.Length > 0) using (var old = new ServiceController(oldName)) {
                wasRunning = old.Status == ServiceControllerStatus.Running;
                c["migration_restart_old"] = wasRunning;
                if (old.Status != ServiceControllerStatus.Stopped) { old.Stop(); old.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(100)); }
            }
            var info = new ProcessStartInfo(Path.Combine(Install,"python","python.exe"), "-B " + Quote(Path.Combine(Install,"desktop","runtime.py")) + " --migrate") {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
                RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Install
            };
            using (var process = Process.Start(info)) {
                process.OutputDataReceived += delegate {}; process.ErrorDataReceived += delegate {};
                process.BeginOutputReadLine(); process.BeginErrorReadLine();
                byte[] payload = Utf8.GetBytes(Json.Serialize(c));
                process.StandardInput.BaseStream.Write(payload,0,payload.Length); process.StandardInput.Close();
                if (!process.WaitForExit(3600000)) { process.Kill(); throw new Exception("La reprise a dépassé son délai. L'ancien dossier et sa base sont conservés."); }
                if (process.ExitCode != 0) {
                    var errorFile = Path.Combine(Root,"private","migration-error.txt");
                    throw new Exception(File.Exists(errorFile) ? File.ReadAllText(errorFile,Utf8) : "Reprise interrompue : source conservée, nouvelle application non démarrée.");
                }
            }
            var resultPath = Path.Combine(Root,"private","migration-result.json");
            var result = Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(resultPath,Utf8));
            c["db_name"] = result["db_name"]; c["application_settings"] = result["settings"];
            c["modules"] = result["modules"] ?? new[] { "presences", "statistiques", "adhesions", "finances", "ressources", "accompagnement", "partenaires", "questionnaires", "transitions", "rh" };
            c["migration_done"] = true; c["migration_pending_activation"] = true; SaveConfiguration(c);
            File.Delete(resultPath);
            // Le rapport consultable ne contient aucun secret ni données nominatives.
            result.Remove("settings");
            File.WriteAllText(Path.Combine(Root,"Direction-DSI","Reprise.json"),Json.Serialize(result),Utf8);
            File.Delete(Path.Combine(Root,"runtime","reprise","complete.json"));
        } catch {
            if (wasRunning && oldName.Length > 0) using (var old = new ServiceController(oldName)) {
                if (old.Status == ServiceControllerStatus.Stopped) old.Start();
            }
            throw;
        }
    }
    internal static void WriteReport(Dictionary<string, object> c) {
        string path = Path.Combine(Root, "Direction-DSI", "Installation-confidentielle.txt"); GuardPath(path);
        var text = new StringBuilder();
        text.AppendLine("MON CENTRE SOCIAL — DOSSIER CONFIDENTIEL DIRECTION / DSI");
        text.AppendLine("Créé le " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        text.AppendLine("Rapport technique sans mots de passe. Conserver avec les sauvegardes hors machine.");
        text.AppendLine("Accès Windows : Administrateurs et SYSTEM uniquement. La DSI peut donner un accès NTFS nominatif à la direction.");
        text.AppendLine("Photographie de l'installation ; les changements effectués ensuite dans l'administration ne sont pas recopiés ici.");
        foreach (var pair in new[] { new[] { "Structure", "organization" }, new[] { "Adresse de connexion administration", "url" }, new[] { "Adresse kiosque tablettes/téléphones", "kiosk_url" }, new[] { "Adresse IPv4 du serveur sur le LAN", "lan_ip" }, new[] { "Compte direction", "admin_email" }, new[] { "Port PostgreSQL (127.0.0.1 exclusivement)", "db_port" }, new[] { "Serveur SMTP", "smtp_host" }, new[] { "Port SMTP / STARTTLS", "smtp_port" }, new[] { "Identifiant SMTP", "smtp_user" }, new[] { "Expéditeur", "smtp_sender" } }) text.AppendLine(pair[0] + " : " + c[pair[1]]);
        text.AppendLine("Compte applicatif non superutilisateur : mcs. La base gérée est indiquée dans la configuration protégée.");
        text.AppendLine("Modules initiaux : " + Json.Serialize(c["modules"]));
        text.AppendLine("Programme : " + Install);
        text.AppendLine("Données : " + Root);
        text.AppendLine("Configuration chiffrée DPAPI : " + ConfigFile);
        text.AppendLine("Pièces jointes : " + Path.Combine(Root, "uploads"));
        text.AppendLine("Sauvegardes : " + Path.Combine(Root, "backups") + " (quotidiennes, 30 lots). Prévoir une copie hors machine dans Administration > Sauvegardes.");
        text.AppendLine("Service Windows : " + ServiceName + " ; compte virtuel NT SERVICE\\" + ServiceName);
        text.AppendLine("Le service démarre avant toute ouverture de session. L'icône apparaît à la connexion Windows.");
        if (Convert.ToBoolean(c["network"])) {
            text.AppendLine("Réseau administration : HTTPS, sous-réseau local, profils Privé/Domaine ; aucune ouverture de PostgreSQL.");
            text.AppendLine("Kiosque mobile : HTTP limité aux routes /kiosk, /static, /media/branding et /healthz, sous-réseau local uniquement. Utiliser l'adresse kiosque ci-dessus depuis le même Wi-Fi ; aucune installation de certificat n'est nécessaire sur les téléphones/tablettes.");
            text.AppendLine("Ne jamais publier le port kiosque sur Internet ni utiliser un Wi-Fi invité non isolé. Pour un accès hors les murs, configurer le tunnel HTTPS décrit dans le guide.");
            text.AppendLine("Certificat public administration à déployer sur les postes clients (magasin Autorités racines de confiance) : " + Path.Combine(Root, "public", "Certificat-du-centre.cer"));
            text.AppendLine("Le nom de serveur doit être résolu par le DNS local pour l'administration. Ne pas contourner les alertes du navigateur.");
        } else {
            text.AppendLine("Réseau : accès limité à cet ordinateur (127.0.0.1) ; aucune ouverture de PostgreSQL.");
        }
        text.AppendLine("Restauration : réinstaller la même version, créer un compte direction, puis Administration > Sauvegardes. La configuration DPAPI dépend de cette machine ; utiliser ce dossier pour reconfigurer après sinistre.");
        text.AppendLine("La désinstallation conserve les données et ce dossier. Guide complet : " + Path.Combine(Install, "GUIDE-WINDOWS.md"));
        File.WriteAllText(path, text.ToString(), Utf8);
    }
    internal static void Open() {
        var path = Path.Combine(Root, "public", "url.txt");
        if (!File.Exists(path)) { Process.Start(new ProcessStartInfo(Exe, "--configure") { UseShellExecute = true, Verb = "runas" }); return; }
        string url = File.ReadAllText(path).Trim(); Uri address;
        if (!Uri.TryCreate(url, UriKind.Absolute, out address) || (address.Scheme != "http" && address.Scheme != "https")) throw new Exception("Adresse locale invalide.");
        Process.Start(new ProcessStartInfo(url + "/admin/instance") { UseShellExecute = true });
    }
}

sealed class CentreService : ServiceBase {
    Process runtime;
    StreamWriter log;
    IntPtr job;
    volatile bool stopping;
    internal CentreService() { ServiceName = Program.ServiceName; CanStop = true; CanShutdown = true; AutoLog = true; }
    protected override void OnStart(string[] args) {
        var c = Program.ReadConfiguration();
        var stop = Path.Combine(Program.Root, "runtime", "stop"); if (File.Exists(stop)) File.Delete(stop);
        var ready = Path.Combine(Program.Root, "runtime", "ready"); if (File.Exists(ready)) File.Delete(ready);
        log = new StreamWriter(Path.Combine(Program.Root, "logs", "service.log"), true, Program.Utf8) { AutoFlush = true };
        var psi = new ProcessStartInfo(Path.Combine(Program.Install, "python", "python.exe"), "-B " + Program.Quote(Path.Combine(Program.Install, "desktop", "runtime.py")) + " --supervise") { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Program.Install, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        psi.EnvironmentVariables.Remove("PYTHONPATH"); psi.EnvironmentVariables.Remove("PYTHONHOME");
        runtime = new Process { StartInfo = psi, EnableRaisingEvents = true };
        runtime.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock(log) log.WriteLine(e.Data); };
        runtime.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock(log) log.WriteLine(e.Data); };
        runtime.Exited += delegate { if (!stopping) Environment.Exit(1); };
        job = Job.Create(); runtime.Start();
        if (!Job.AssignProcessToJobObject(job, runtime.Handle)) { runtime.Kill(); throw new Exception("Protection des processus enfants impossible."); }
        runtime.BeginOutputReadLine(); runtime.BeginErrorReadLine();
        byte[] configBytes = Program.Utf8.GetBytes(Program.Json.Serialize(c));
        runtime.StandardInput.BaseStream.Write(configBytes, 0, configBytes.Length); runtime.StandardInput.Close();
    }
    protected override void OnStop() {
        stopping = true; RequestAdditionalTime(95000);
        File.WriteAllText(Path.Combine(Program.Root, "runtime", "stop"), "1");
        if (runtime != null && !runtime.WaitForExit(85000)) runtime.Kill();
        if (job != IntPtr.Zero) { Job.CloseHandle(job); job = IntPtr.Zero; }
        if (log != null) log.Dispose();
    }
    protected override void OnShutdown() { OnStop(); }
}

// Tous les descendants sont arrêtés si Windows termine brutalement le service.
static class Job {
    [StructLayout(LayoutKind.Sequential)] struct Basic { public long ProcessTime, JobTime; public uint Flags; public UIntPtr Min, Max; public uint Active; public UIntPtr Affinity; public uint Priority, Scheduling; }
    [StructLayout(LayoutKind.Sequential)] struct IO { public ulong a,b,c,d,e,f; }
    [StructLayout(LayoutKind.Sequential)] struct Extended { public Basic Basic; public IO IO; public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string n);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr j, int c, IntPtr p, uint n);
    [DllImport("kernel32.dll")] internal static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
    [DllImport("kernel32.dll")] internal static extern bool CloseHandle(IntPtr h);
    internal static IntPtr Create() { var job = CreateJobObject(IntPtr.Zero, null); var data = new Extended(); data.Basic.Flags = 0x2000; int size = Marshal.SizeOf(data); var p = Marshal.AllocHGlobal(size); try { Marshal.StructureToPtr(data,p,false); if (!SetInformationJobObject(job,9,p,(uint)size)) throw new Exception("Impossible de créer le groupe de processus."); } finally { Marshal.FreeHGlobal(p); } return job; }
}

sealed class Tray : ApplicationContext {
    readonly NotifyIcon icon;
    internal Tray() {
        icon = new NotifyIcon { Icon = Program.Logo, Text = "Mon Centre Social", Visible = true };
        var menu = new ContextMenuStrip();
        menu.Items.Add("Ouvrir la page d'administration", null, delegate { Safely(Program.Open); });
        menu.Items.Add("Redémarrer", null, delegate { Safely(delegate { Control("--restart"); }); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Fermer", null, delegate { Safely(delegate { if (Control("--stop")) { icon.Visible = false; ExitThread(); } }); });
        icon.ContextMenuStrip = menu; icon.DoubleClick += delegate { Safely(Program.Open); };
    }
    bool Control(string mode) { var p = Process.Start(new ProcessStartInfo(Program.Exe, mode) { UseShellExecute = true, Verb = "runas" }); p.WaitForExit(); return p.ExitCode == 0; }
    void Safely(Action action) { try { action(); } catch (System.ComponentModel.Win32Exception) { } catch (Exception e) { icon.ShowBalloonTip(6000, "Mon Centre Social", e.Message, ToolTipIcon.Warning); } }
    protected override void Dispose(bool disposing) { if (disposing) icon.Dispose(); base.Dispose(disposing); }
}

sealed class InstallationChoice : Form {
    readonly RadioButton fresh = new RadioButton { Text = "Nouvelle installation", Checked = true };
    readonly RadioButton existing = new RadioButton { Text = "Reprendre une ancienne installation de cet ERP" };
    readonly TextBox folder = new TextBox(); readonly TextBox connection = new TextBox(); readonly TextBox service = new TextBox();
    readonly CheckBox maintenance = new CheckBox { Text = "Les saisies et tâches de modification seront suspendues pendant la reprise." };
    internal string Source { get { return existing.Checked ? folder.Text.Trim() : ""; } }
    internal string Connection { get { return existing.Checked ? connection.Text.Trim() : ""; } }
    internal string LegacyService { get { return existing.Checked ? service.Text.Trim() : ""; } }
    internal InstallationChoice() {
        Text = "Mon Centre Social — Votre installation"; ClientSize = new Size(720,445);
        Font = new Font("Segoe UI",10); StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        fresh.SetBounds(24,15,660,30); existing.SetBounds(24,50,660,30); Controls.Add(fresh); Controls.Add(existing);
        Controls.Add(new Label { Text = "Dossier de l'ancienne application (contenant son fichier .env)", Location = new Point(24,96), AutoSize = true });
        folder.SetBounds(24,122,570,28); Controls.Add(folder);
        var browse = new Button { Text = "Parcourir…", Location = new Point(600,121), Size = new Size(100,30) };
        browse.Click += delegate { using (var dialog = new FolderBrowserDialog()) { if (dialog.ShowDialog() == DialogResult.OK) { folder.Text = dialog.SelectedPath; existing.Checked = true; } } }; Controls.Add(browse);
        Controls.Add(new Label { Text = "Connexion PostgreSQL (facultative si elle figure déjà dans .env)", Location = new Point(24,166), AutoSize = true });
        connection.SetBounds(24,192,676,28); connection.UseSystemPasswordChar = true; Controls.Add(connection);
        Controls.Add(new Label { Text = "Nom de l'ancien service Windows (vide si vous l'avez déjà arrêté)", Location = new Point(24,234), AutoSize = true });
        service.SetBounds(24,260,676,28); Controls.Add(service);
        maintenance.SetBounds(24,301,676,30); Controls.Add(maintenance);
        Controls.Add(new Label { Text = "L'ancien service sera arrêté puis désactivé après réussite. La base source reste conservée.\nEn cas d'échec, le service précédemment actif est relancé. PostgreSQL 10 à 17 pris en charge.", Location = new Point(24,340), Size = new Size(676,50) });
        var next = new Button { Text = "Continuer", Location = new Point(570,400), Size = new Size(130,30) };
        next.Click += delegate {
            if (existing.Checked && (!Directory.Exists(Source) || !maintenance.Checked)) { MessageBox.Show("Sélectionnez le dossier source et confirmez l'interruption des saisies."); return; }
            DialogResult = DialogResult.OK; Close();
        }; Controls.Add(next); AcceptButton = next;
    }
}

sealed class SetupWizard : Form {
    readonly string migrationSource, migrationUri, migrationService;
    readonly Panel content = new Panel(); readonly Label heading = new Label(); readonly Label stepLabel = new Label();
    readonly Button next = new Button(); readonly Button back = new Button(); readonly Label status = new Label();
    readonly TextBox organization = new TextBox(); readonly TextBox adminName = new TextBox(); readonly TextBox email = new TextBox(); readonly TextBox password = new TextBox(); readonly TextBox confirm = new TextBox();
    readonly CheckedListBox modules = new CheckedListBox(); readonly RadioButton local = new RadioButton(); readonly RadioButton network = new RadioButton(); readonly TextBox hostname = new TextBox();
    readonly TextBox smtpHost = new TextBox(); readonly TextBox smtpPort = new TextBox(); readonly TextBox smtpUser = new TextBox(); readonly TextBox smtpPassword = new TextBox(); readonly TextBox smtpSender = new TextBox();
    readonly string[] keys = { "presences", "statistiques", "adhesions", "finances", "ressources", "accompagnement", "partenaires", "questionnaires", "transitions", "rh" };
    readonly string[] names = { "Accueil, inscriptions et présences (toujours inclus)", "Statistiques et bilans", "Adhésions, caisse et impayés", "Finances et projets", "Salles et matériel", "Accompagnement et pédagogie", "Partenaires", "Questionnaires", "Transitions", "Ressources humaines" };
    // Profils : mêmes listes que PROFILES dans app/services/modules.py.
    static readonly string[] profilEssentiel = { "presences", "statistiques" };
    static readonly string[] profilAnimation = { "presences", "statistiques", "adhesions", "ressources", "partenaires", "accompagnement", "questionnaires" };
    int step; bool busy;
    internal SetupWizard(string source = "", string sourceUri = "", string oldService = "") {
        migrationSource = source; migrationUri = sourceUri; migrationService = oldService;
        Text = "Mon Centre Social — Installation"; Icon = Program.Logo; ClientSize = new Size(780, 650); AutoScaleMode = AutoScaleMode.Dpi; Font = new Font("Segoe UI", 10); BackColor = Color.White; StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
        var banner = new Panel { Dock = DockStyle.Top, Height = 102, BackColor = Color.FromArgb(19,91,99) };
        banner.Controls.Add(new PictureBox { Image = Image.FromFile(Path.Combine(Program.Install,"mon-centre-social.png")), SizeMode = PictureBoxSizeMode.Zoom, Location = new Point(28,22), Size = new Size(58,58) });
        banner.Controls.Add(new Label { Text = "Mon Centre Social", ForeColor = Color.White, Font = new Font("Segoe UI", 23, FontStyle.Bold), Location = new Point(104,15), AutoSize = true });
        banner.Controls.Add(new Label { Text = "Les outils utiles, au rythme de votre équipe.", ForeColor = Color.White, Location = new Point(108,62), AutoSize = true });
        Controls.Add(banner);
        stepLabel.SetBounds(32,116,710,24); stepLabel.ForeColor = Color.FromArgb(85,105,112); Controls.Add(stepLabel);
        heading.SetBounds(30,150,720,40); heading.Font = new Font("Segoe UI",20,FontStyle.Bold); Controls.Add(heading);
        content.SetBounds(32,205,716,345); Controls.Add(content);
        status.SetBounds(32,555,710,44); status.ForeColor = Color.FromArgb(19,91,99); Controls.Add(status);
        back.Text = "Précédent"; back.SetBounds(470,607,125,32); back.Click += delegate { if (step > 0) { step--; ShowStep(); } }; Controls.Add(back);
        next.Text = "Continuer"; next.SetBounds(609,607,140,32); next.BackColor = Color.FromArgb(19,91,99); next.ForeColor = Color.White; next.FlatStyle = FlatStyle.Flat; next.Click += async delegate { await Next(); }; Controls.Add(next); AcceptButton = next;
        password.UseSystemPasswordChar = true; confirm.UseSystemPasswordChar = true; smtpPassword.UseSystemPasswordChar = true; smtpPort.Text = "587"; hostname.Text = Environment.MachineName.ToLowerInvariant(); local.Checked = true;
        modules.CheckOnClick = true; modules.BorderStyle = BorderStyle.None; modules.Items.AddRange(names); modules.SetItemChecked(0,true); modules.SetItemChecked(1,true);
        // Le socle (présences) ne se décoche pas : tout le reste s'appuie dessus.
        modules.ItemCheck += delegate(object sender, ItemCheckEventArgs e) { if (e.Index == 0) e.NewValue = CheckState.Checked; };
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
        ShowStep();
    }
    void Profil(string[] codes) { for (int i=0;i<keys.Length;i++) modules.SetItemChecked(i, i == 0 || Array.IndexOf(codes, keys[i]) >= 0); }
    void TextLine(string text, int y, int height = 48) { content.Controls.Add(new Label { Text = text, Location = new Point(0,y), Size = new Size(706,height) }); }
    void Field(string title, TextBox box, int y, int x = 0, int width = 342) { content.Controls.Add(new Label { Text = title, Location = new Point(x,y), AutoSize = true }); box.SetBounds(x,y+25,width,27); box.MaxLength = box == password || box == confirm || box == smtpPassword ? 200 : 180; content.Controls.Add(box); }
    void ShowStep() {
        content.Controls.Clear(); status.Text = ""; back.Enabled = step > 0; next.Text = step == 4 ? "Installer" : "Continuer"; stepLabel.Text = "ÉTAPE " + (step+1) + " SUR 5";
        if (step == 0) {
            if (migrationSource.Length > 0) {
                heading.Text = "Reprendre votre centre";
                TextLine("Source : " + migrationSource,10,65);
                TextLine("Les comptes, mots de passe et données existants seront conservés. Aucun nouveau compte direction n'est créé.",90,70);
                TextLine("L'assistant vérifiera l'historique des migrations, les données copiées et les documents avant de démarrer la nouvelle application. Une base inconnue ou incomplète bloque la reprise.",180,100);
                return;
            }
            heading.Text = "Bienvenue dans votre centre";
            TextLine("Créez le compte de la direction. Vous ajouterez les membres de l'équipe ensuite, avec leurs propres droits.",0);
            Field("Nom de la structure",organization,60,0,706); Field("Nom de la personne responsable",adminName,128); Field("Adresse e-mail de connexion",email,128,365);
            Field("Mot de passe (12 caractères minimum)",password,201); Field("Confirmer le mot de passe",confirm,201,365);
            TextLine("Les composants nécessaires sont inclus. Une connexion Internet n'est pas nécessaire pour installer.",286);
        } else if (step == 1) {
            if (migrationSource.Length > 0) {
                heading.Text = "Vos outils sont conservés";
                TextLine("Les modules actifs seront repris depuis votre base. Vous pourrez ensuite les ajuster dans l'administration.",15,90);
                return;
            }
            heading.Text = "De quels outils avez-vous besoin ?";
            TextLine("Partez d'un profil, puis ajustez si besoin. Vous pourrez changer ce choix plus tard, sans réinstaller et sans rien perdre.",0);
            var essentiel = new Button { Text = "Présences et statistiques", Location = new Point(480,62), Size = new Size(226,32) }; essentiel.Click += delegate { Profil(profilEssentiel); }; content.Controls.Add(essentiel);
            var animation = new Button { Text = "Animation et accueil", Location = new Point(480,102), Size = new Size(226,32) }; animation.Click += delegate { Profil(profilAnimation); }; content.Controls.Add(animation);
            var all = new Button { Text = "Tous les outils", Location = new Point(480,142), Size = new Size(226,32) }; all.Click += delegate { Profil(keys); }; content.Controls.Add(all);
            modules.SetBounds(0,62,465,280); modules.ItemHeight = 27; content.Controls.Add(modules);
        } else if (step == 2) {
            heading.Text = "Où l'équipe utilisera-t-elle l'application ?";
            local.Text = "Sur cet ordinateur uniquement"; local.SetBounds(0,16,670,34); content.Controls.Add(local);
            network.Text = "Sur plusieurs postes du réseau de la structure"; network.SetBounds(0,66,700,34); content.Controls.Add(network);
            Field("Nom de cet ordinateur sur le réseau",hostname,127,0,500);
            TextLine("En réseau, l'administration est chiffrée (HTTPS). Pour l'émargement, les téléphones et tablettes utilisent une adresse locale dédiée, sans certificat à installer. Le certificat d'administration et les instructions sont fournis automatiquement.",205,75);
            TextLine("Le service fonctionne aussi sans session ouverte sur Windows Server.",292);
        } else if (step == 3) {
            if (migrationSource.Length > 0) {
                heading.Text = "Vos paramètres sont conservés";
                TextLine("Les réglages de messagerie et les autres paramètres compatibles seront lus dans le fichier .env de l'ancienne installation. Les réglages enregistrés en base sont également conservés.",15,110);
                TextLine("Si votre ancien service définit des paramètres hors de ce fichier, reportez-les dans la configuration avant la bascule.",145,100);
                return;
            }
            heading.Text = "Les e-mails de votre structure";
            TextLine("Facultatif : laissez ces champs vides pour configurer l'envoi d'e-mails plus tard depuis l'administration.",0);
            Field("Serveur SMTP",smtpHost,67); Field("Port STARTTLS",smtpPort,67,365,140);
            Field("Identifiant SMTP",smtpUser,145); Field("Mot de passe SMTP",smtpPassword,145,365);
            Field("Adresse d'expédition",smtpSender,224,0,706);
            TextLine("La connexion au serveur mail utilisera STARTTLS avec validation du certificat.",303);
        } else {
            heading.Text = "Tout est prêt";
            TextLine(migrationSource.Length > 0 ? "Reprise : " + migrationSource + "\nComptes, outils et documents conservés." : "Structure : " + organization.Text + "\nCompte direction : " + email.Text,8,60);
            TextLine("Accès : " + (network.Checked ? "réseau local sécurisé" : "cet ordinateur uniquement"),84,60);
            TextLine("L'installation prépare la base, le démarrage automatique et les sauvegardes quotidiennes.",162,58);
            TextLine("Votre rapport technique (adresses et chemins, sans mots de passe) sera créé dans :\n" + Path.Combine(Program.Root,"Direction-DSI"),230,90);
            status.Text = "Seuls les administrateurs Windows peuvent lire le dossier confidentiel.";
        }
    }
    void ValidateStep() {
        if (step == 0 && migrationSource.Length == 0) {
            if (organization.Text.Trim().Length == 0 || adminName.Text.Trim().Length == 0) throw new Exception("Indiquez la structure et le nom de la personne responsable.");
            var parsed = new MailAddress(email.Text.Trim()); if (parsed.Address != email.Text.Trim()) throw new Exception("Vérifiez l'adresse e-mail.");
            if (string.IsNullOrWhiteSpace(password.Text) || password.Text.Length < 12 || password.Text != confirm.Text) throw new Exception("Utilisez au moins 12 caractères et confirmez le même mot de passe.");
        }
        if (step == 1 && modules.CheckedItems.Count == 0) throw new Exception("Choisissez au moins un outil pour démarrer.");
        if (step == 2 && !Regex.IsMatch(hostname.Text.Trim(), "^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")) throw new Exception("Vérifiez le nom de l'ordinateur (lettres, chiffres, points et tirets).");
        if (step == 3 && smtpHost.Text.Trim().Length > 0) {
            int port; if (!int.TryParse(smtpPort.Text,out port) || port < 1 || port > 65535 || port == 465) throw new Exception("Indiquez le port STARTTLS du serveur mail, généralement 587.");
            if (!Regex.IsMatch(smtpHost.Text.Trim(), "^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")) throw new Exception("Vérifiez le nom du serveur SMTP.");
            var sender = new MailAddress(smtpSender.Text.Trim()); if (sender.Address != smtpSender.Text.Trim()) throw new Exception("Vérifiez l'adresse d'expédition.");
        }
    }
    async Task Next() {
        try {
            ValidateStep();
            if (step < 4) { step++; ShowStep(); return; }
            var selected = new List<string>(); for (int i=0;i<keys.Length;i++) if (modules.GetItemChecked(i)) selected.Add(keys[i]);
            if (!selected.Contains("presences")) selected.Insert(0, "presences"); // socle toujours actif
            var c = new Dictionary<string,object> { {"organization",organization.Text.Trim()}, {"admin_name",adminName.Text.Trim()}, {"admin_email",email.Text.Trim().ToLowerInvariant()}, {"admin_password",password.Text}, {"modules",selected.ToArray()}, {"network",network.Checked}, {"hostname",hostname.Text.Trim().ToLowerInvariant()}, {"smtp_host",smtpHost.Text.Trim()}, {"smtp_port",string.IsNullOrWhiteSpace(smtpHost.Text) ? 587 : int.Parse(smtpPort.Text)}, {"smtp_user",smtpUser.Text.Trim()}, {"smtp_password",smtpPassword.Text}, {"smtp_sender",smtpSender.Text.Trim()} };
            if (migrationSource.Length > 0) { c["migration_source"] = migrationSource; c["migration_uri"] = migrationUri; c["migration_service"] = migrationService; }
            busy = true; next.Enabled = false; back.Enabled = false; UseWaitCursor = true;
            await Task.Run(delegate { Program.InstallConfiguration(c, message => BeginInvoke(new Action(delegate { status.Text = message; }))); });
            busy = false; UseWaitCursor = false;
            string mobile = (bool)c["network"] ? "\n\nKiosque tablettes/téléphones (même Wi-Fi) : " + c["kiosk_url"] + "/kiosk/" : "";
            MessageBox.Show("Votre centre est prêt.\n\nAdresse administration : " + c["url"] + mobile + "\n\nLe dossier confidentiel est dans :\n" + Path.Combine(Program.Root,"Direction-DSI") + "\n\nL'icône Mon Centre Social permet d'ouvrir l'administration, de redémarrer et de fermer le service.", "Installation terminée", MessageBoxButtons.OK, MessageBoxIcon.Information);
            DialogResult = DialogResult.OK; Close();
        } catch (Exception e) { busy = false; UseWaitCursor = false; next.Enabled = true; back.Enabled = true; status.Text = e.Message; MessageBox.Show(e.Message,"Mon Centre Social",MessageBoxButtons.OK,MessageBoxIcon.Warning); }
    }
}
}
