"""Service Minitel : page « Services » alimentée par les fichiers .vdt.

Chaque fichier .vdt posé dans le dossier vdt/ apparaît tout seul dans la liste,
avec un numéro. Le numéro est mémorisé dans vdt/services.json : il ne change
plus, même si on ajoute d'autres fichiers. Pas besoin de redémarrer le serveur.

Une instance de Session est créée par connexion. start() renvoie la première
page ; on_input(octets) reçoit les touches du Minitel et renvoie les octets à
afficher (ou b"" s'il n'y a rien à envoyer).
"""
import json
import os

NOIR, ROUGE, VERT, JAUNE, BLEU, MAGENTA, CYAN, BLANC = range(8)
CLS = b"\x0c"
CON = b"\x11"
COFF = b"\x14"
DOUBLE_HAUTEUR = b"\x1b\x4d"
NORMAL = b"\x1b\x4c"

BASE = os.path.dirname(os.path.abspath(__file__))
DOSSIER = os.path.join(BASE, "vdt")
REGISTRE = os.path.join(DOSSIER, "services.json")
PAR_PAGE = 14
TAILLE_MAX = 256 * 1024
CHAMP = (20, 10)  # ligne, colonne du champ de saisie

ACCENTS = {
    "é": (0x42, "e"), "è": (0x41, "e"), "ê": (0x43, "e"), "ë": (0x48, "e"),
    "à": (0x41, "a"), "â": (0x43, "a"), "î": (0x43, "i"), "ï": (0x48, "i"),
    "ô": (0x43, "o"), "ù": (0x41, "u"), "û": (0x43, "u"), "ç": (0x4B, "c"),
}


def enc(texte):
    """Texte Python -> octets Minitel (accents via le jeu G2)."""
    sortie = bytearray()
    for ch in texte:
        if ch in ACCENTS:
            accent, lettre = ACCENTS[ch]
            sortie += bytes([0x19, accent]) + lettre.encode()
        else:
            sortie += ch.encode("ascii", "replace")
    return bytes(sortie)


def at(ligne, col):
    """Positionne le curseur (ligne 1-24, colonne 1-40)."""
    return bytes([0x1F, 0x40 + ligne, 0x40 + col])


def fg(couleur):
    return bytes([0x1B, 0x40 + couleur])


def bg(couleur):
    return bytes([0x1B, 0x50 + couleur])


def bandeau(ligne, texte, fond=BLEU, encre=BLANC):
    """Ligne pleine largeur avec fond coloré (le 1er caractère porte l'attribut)."""
    return at(ligne, 1) + bg(fond) + fg(encre) + enc(texte.ljust(39)[:39])


def charger_services():
    """Renvoie {numéro: nom de fichier}. Un nouveau .vdt reçoit le numéro suivant."""
    os.makedirs(DOSSIER, exist_ok=True)
    try:
        with open(REGISTRE, encoding="utf-8") as f:
            registre = {int(n): nom for n, nom in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        registre = {}
    avant = dict(registre)
    presents = {n for n in os.listdir(DOSSIER) if n.lower().endswith(".vdt")}
    registre = {n: nom for n, nom in registre.items() if nom in presents}
    suivant = max(registre, default=0) + 1
    for nom in sorted(presents - set(registre.values()), key=str.lower):
        registre[suivant] = nom
        suivant += 1
    if registre != avant:
        try:
            with open(REGISTRE, "w", encoding="utf-8") as f:
                json.dump({str(n): nom for n, nom in sorted(registre.items())},
                          f, ensure_ascii=False, indent=1)
        except OSError:
            pass
    return dict(sorted(registre.items()))


def titre(nom):
    return os.path.splitext(nom)[0].replace("_", " ")[:30]


class Session:
    def __init__(self):
        self.saisie = ""
        self.page = 0
        self.mode = "liste"
        self.services = {}

    def start(self):
        return self.liste()

    def liste(self, message=""):
        self.mode, self.saisie = "liste", ""
        self.services = charger_services()
        numeros = list(self.services)
        nb_pages = max(1, -(-len(numeros) // PAR_PAGE))
        self.page = min(self.page, nb_pages - 1)
        ecran = CLS + bandeau(1, " SERVICES")
        if not numeros:
            ecran += (at(5, 2) + fg(JAUNE) + enc("Aucun service pour l'instant.")
                      + at(7, 2) + fg(BLANC) + enc("Ajoutez des fichiers .vdt dans")
                      + at(8, 2) + enc("le dossier vdt/ du serveur."))
        debut = self.page * PAR_PAGE
        for i, n in enumerate(numeros[debut:debut + PAR_PAGE]):
            ecran += (at(3 + i, 2) + fg(JAUNE) + enc(str(n).rjust(3))
                      + fg(BLANC) + enc(" " + titre(self.services[n])))
        if nb_pages > 1:
            ecran += (at(18, 2) + fg(CYAN)
                      + enc(f"Page {self.page + 1}/{nb_pages} - Suite / Retour"))
        if message:
            ecran += at(22, 2) + fg(ROUGE) + enc(message)
        return (ecran + at(20, 2) + fg(CYAN) + enc("Numéro : ") + fg(BLANC)
                + bandeau(24, " Tapez le numéro puis Envoi", NOIR, VERT)
                + at(*CHAMP) + CON)

    def afficher(self, numero):
        chemin = os.path.join(DOSSIER, self.services[numero])
        try:
            with open(chemin, "rb") as f:
                contenu = f.read(TAILLE_MAX)
        except OSError:
            return self.liste("Fichier illisible")
        self.mode = "vdt"
        return CLS + COFF + contenu

    def touche(self, code):
        if code == 0x46:  # Sommaire : toujours la liste
            self.page = 0
            return self.liste()
        if self.mode == "vdt":  # Retour / Annulation : retour à la liste
            return self.liste() if code in (0x42, 0x45) else b""
        if code == 0x41:  # Envoi
            texte, self.saisie = self.saisie, ""
            if texte.isdigit() and int(texte) in self.services:
                return self.afficher(int(texte))
            return self.liste("Numéro inconnu")
        if code == 0x47 and self.saisie:  # Correction
            self.saisie = self.saisie[:-1]
            return b"\x08 \x08"
        if code == 0x45:  # Annulation
            n, self.saisie = len(self.saisie), ""
            return at(*CHAMP) + b" " * n + at(*CHAMP)
        if code == 0x48:  # Suite
            self.page += 1
            return self.liste()
        if code == 0x42:  # Retour
            self.page = max(0, self.page - 1)
            return self.liste()
        return b""

    def on_input(self, data):
        sortie = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0x13 and i + 1 < len(data):  # touche de fonction
                sortie += self.touche(data[i + 1])
                i += 2
            elif b == 0x1B:  # flèches : ignorées
                i += 3 if data[i + 1:i + 2] == b"[" else 1
            else:
                if self.mode == "liste" and 0x30 <= b <= 0x39 and len(self.saisie) < 4:
                    self.saisie += chr(b)
                    sortie.append(b)
                i += 1
        return bytes(sortie)
