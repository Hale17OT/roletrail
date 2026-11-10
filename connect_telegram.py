"""Local Telegram connection window. Sensitive values never enter chat or logs."""
import asyncio
import re
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from discovery.telegram_auth import (enable_adapter, load_auth, save_auth, write_status)


def main():
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
    from telethon.sessions import StringSession

    root = tk.Tk()
    root.title("Roletrail — Connect Telegram")
    root.geometry("570x430")
    root.minsize(510, 400)
    root.configure(bg="#131518")
    frame = tk.Frame(root, bg="#131518", padx=24, pady=24)
    frame.pack(fill="both", expand=True)
    label = lambda text, **kw: tk.Label(frame, text=text, bg="#131518", fg="#f1f3f7", anchor="w", justify="left", **kw)
    label("Connect Telegram", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    label("1. Sign in at my.telegram.org and open API development tools.\n"
          "2. Enter your app's API ID and API hash below.\n"
          "3. Complete the login prompts locally.", font=("Segoe UI", 10), wraplength=510).pack(anchor="w", pady=(12, 16))
    label("API ID", font=("Segoe UI", 10)).pack(anchor="w")
    api_id_entry = ttk.Entry(frame)
    api_id_entry.pack(fill="x", pady=(4, 12))
    label("API hash", font=("Segoe UI", 10)).pack(anchor="w")
    api_hash_entry = ttk.Entry(frame, show="•")
    api_hash_entry.pack(fill="x", pady=(4, 12))
    label("Your account session will be encrypted for this Windows user.\n"
          "Roletrail reads configured job channels; it does not send messages\n"
          "or join channels. Revoke the session in Telegram → Settings → Devices.",
          font=("Segoe UI", 9), wraplength=510).pack(anchor="w", pady=(0, 12))
    status = tk.StringVar(value="Waiting for your API credentials. Do not paste them into chat.")
    label("", font=("Segoe UI", 9), wraplength=510, textvariable=status).pack(anchor="w")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def ask(title, prompt, secret=False):
        value = simpledialog.askstring(title, prompt, parent=root, show="•" if secret else None)
        if value is None:
            raise InterruptedError("Login cancelled")
        return value.strip()

    def connect():
        api_id, api_hash = api_id_entry.get().strip(), api_hash_entry.get().strip()
        if not api_id.isdigit() or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
            messagebox.showerror("API credentials", "Enter the numeric api_id and the 32-character api_hash from your own API development tools page.", parent=root)
            return
        button.configure(state="disabled")
        client = None
        try:
            status.set("Connecting to Telegram…")
            root.update_idletasks()
            write_status("connecting")
            client = TelegramClient(StringSession(), int(api_id), api_hash, loop=loop,
                                    flood_sleep_threshold=0, receive_updates=False,
                                    device_model="Roletrail Career Dashboard", app_version="1.0")
            loop.run_until_complete(client.connect())
            phone = ask("Telegram phone number", "Enter your Telegram phone number in international format (for example +251…).")
            loop.run_until_complete(client.send_code_request(phone))
            write_status("awaiting_login_code")
            for attempt in range(3):
                code = ask("Telegram login code", "Enter the login code Telegram sent you. Enter it only in this local window.", secret=True)
                try:
                    loop.run_until_complete(client.sign_in(phone=phone, code=code))
                    break
                except PhoneCodeInvalidError:
                    if attempt == 2:
                        raise
                    messagebox.showerror("Invalid code", "That code was not accepted. Try again locally.", parent=root)
                except SessionPasswordNeededError:
                    write_status("awaiting_two_step_password")
                    password = ask("Telegram two-step verification", "Enter your existing Telegram two-step verification password.", secret=True)
                    loop.run_until_complete(client.sign_in(password=password))
                    break
            if not loop.run_until_complete(client.is_user_authorized()):
                raise RuntimeError("Authorization incomplete")
            # Read one public job-channel post to verify access, without joining or messaging.
            status.set("Signed in. Checking the public job channel reader…")
            root.update_idletasks()
            read_ok = False
            try:
                messages = loop.run_until_complete(client.get_messages("hahujobs", limit=1))
                read_ok = bool(messages)
            except Exception:
                pass
            save_auth(int(api_id), api_hash, client.session.save())
            enable_adapter()
            write_status("connected", channelReadVerified=read_ok, adapter="mtproto")
            api_id_entry.delete(0, "end")
            api_hash_entry.delete(0, "end")
            status.set("Connected. Authenticated Telegram collection is enabled.")
            messagebox.showinfo("Telegram connected", "Your encrypted session is saved and Roletrail now uses the authenticated channel reader.\n\nNo jobs have been added during setup.", parent=root)
            root.destroy()
        except InterruptedError:
            write_status("cancelled")
            status.set("Login cancelled. You can try again when ready.")
        except PhoneCodeExpiredError:
            write_status("needs_retry", reason="login_code_expired")
            status.set("The login code expired. Click Connect to request a new code.")
        except Exception as exc:
            # Exception names are useful diagnostics, without persisting credential-bearing messages.
            write_status("needs_retry", reason=type(exc).__name__)
            status.set(f"Connection could not complete ({type(exc).__name__}). Try again or ask for help.")
        finally:
            if client:
                loop.run_until_complete(client.disconnect())
            try:
                button.configure(state="normal")
            except tk.TclError:
                pass

    button = ttk.Button(frame, text="Connect and save encrypted session", command=connect)
    button.pack(anchor="w", pady=(16, 0))
    write_status("awaiting_api_credentials")
    root.mainloop()
    loop.close()


if __name__ == "__main__":
    main()
