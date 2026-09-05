import tkinter as tk
from tkinter import ttk


def make_button(parent, text, command=None, **kwargs):
    defaults = dict(
        bg="#E0E0E0",
        fg="black",
        relief=tk.RAISED,
        activebackground="#BDBDBD",
        activeforeground="black",
    )
    defaults.update(kwargs)
    return tk.Button(parent, text=text, command=command, **defaults)


def make_status_label(parent, text="断开"):
    return tk.Label(parent, text=text, fg="red", font=("Arial", 9, "bold"))


def make_icon_button(parent, text, command=None, width=None, **kwargs):
    return tk.Button(parent, text=text, command=command, width=width, **kwargs)
