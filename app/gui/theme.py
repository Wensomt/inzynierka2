"""Wspólny, jasny wygląd zwykłej aplikacji ttk."""

from tkinter import font as tkfont, ttk


COLORS = {
    "bg": "#f3f3f3", "panel": "#f3f3f3", "panel_alt": "#e5e5e5",
    "field": "#ffffff", "fg": "#202020", "muted": "#59636e",
    "blue": "#2166a0", "green": "#287441", "yellow": "#8a5a12", "red": "#b13939",
}


def configure_theme(root):
    style = ttk.Style(root)
    style.theme_use("vista" if "vista" in style.theme_names() else "clam")
    family = tkfont.nametofont("TkDefaultFont").cget("family")
    root.configure(background=COLORS["bg"])
    style.configure(".", font=(family, 10))
    style.configure("TFrame", background=COLORS["panel"])
    style.configure("TLabel", background=COLORS["panel"], foreground=COLORS["fg"])
    style.configure("Title.TLabel", font=(family, 14, "bold"))
    style.configure("Status.TLabel", foreground=COLORS["blue"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"], font=(family, 9))
    style.configure("TButton", padding=(8, 4))
    style.configure("Accent.TButton", font=(family, 10, "bold"), padding=(10, 5))
    style.configure("TNotebook.Tab", padding=(11, 5))
    style.configure("TLabelframe", background=COLORS["panel"])
    style.configure("TLabelframe.Label", background=COLORS["panel"], foreground=COLORS["muted"])
    style.configure("Treeview", rowheight=25, font=(family, 9))
    style.configure("Treeview.Heading", font=(family, 9, "bold"))
    style.map("Treeview", background=[("selected", COLORS["blue"])], foreground=[("selected", "#ffffff")])
    # Te widgety Tk (w odróżnieniu od ttk) nie dziedziczą motywu systemowego.
    for widget in ("Text", "Listbox"):
        root.option_add("*{}.background".format(widget), COLORS["field"])
        root.option_add("*{}.foreground".format(widget), COLORS["fg"])
    return (tkfont.nametofont("TkFixedFont").cget("family"), 9)
