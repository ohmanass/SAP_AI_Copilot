/* Offline page (offline.html): texts in the language of the app, reload when back online. */
// Same languages as the reviewed translations of the app
const texts = {
	fr: ["Vous êtes hors connexion", "SAP AI Copilot a besoin d’une connexion pour joindre SAP et le modèle d’IA. Vérifiez votre réseau, puis réessayez.", "Réessayer"],
	de: ["Sie sind offline", "SAP AI Copilot benötigt eine Verbindung zu SAP und zum KI-Modell. Prüfen Sie Ihr Netzwerk und versuchen Sie es erneut.", "Erneut versuchen"],
	es: ["No hay conexión", "SAP AI Copilot necesita conexión para acceder a SAP y al modelo de IA. Compruebe su red e inténtelo de nuevo.", "Reintentar"]
};
const chosen = (() => { try { return localStorage.getItem("sap-ai-copilot.language"); } catch (e) { return null; } })();
const language = [chosen, ...(navigator.languages || [])].filter(Boolean).map((tag) => tag.slice(0, 2)).find((code) => texts[code]);
if (language) {
	document.documentElement.lang = language;
	[document.getElementById("title").textContent, document.getElementById("text").textContent,
		document.getElementById("retry").textContent] = texts[language];
}
document.getElementById("retry").addEventListener("click", () => window.location.reload());
window.addEventListener("online", () => window.location.reload());
