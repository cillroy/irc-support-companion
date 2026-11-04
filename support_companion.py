"""Support Companion: local JSON case manager + mIRC handoff."""
from __future__ import annotations
import argparse, json, os, re, tempfile, uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote
import tkinter as tk
from tkinter import ttk, messagebox

TEMPLATES={
 "basic":"mods can you confirm the email {email} matches the account {profile}",
 "disabled":"mods can you confirm the email {email} matches the account {profile} : Speed Test {speedtest} : the account has been disabled and would like to resolve the issue",
 "ratio":"mods can you confirm the email {email} matches the account {profile} : Speed Test {speedtest} : the account was disabled due to a low ratio and would like to fix it",
}
MODE_LABELS={"Basic":"basic","Disabled":"disabled","Low Ratio":"ratio"}
REV_MODE={v:k for k,v in MODE_LABELS.items()}
STATUSES=("Started","Waiting","Ready","Submitted","Closed")
ICONS={"new":"＋","rebuild":"↻","save":"▣","send":"➜","search":"⌕","theme":"◐","filter":"≡","ok":"✓","error":"!","info":"i"}
THEMES={"Dark":{"bg":"#16181d","panel":"#20232a","field":"#2a2e36","text":"#f2f3f5","muted":"#aeb4bf","accent":"#c92a3d","badge_fg":"#ffffff"},"Light":{"bg":"#f3f4f6","panel":"#ffffff","field":"#ffffff","text":"#202124","muted":"#667085","accent":"#b51f32","badge_fg":"#ffffff"}}

UI={"outer":18,"card_gap":14,"field_y":4,"label_gap":8,"input_pad_y":5,"modal_pad":18}
DEFAULT_CONFIG={
 "branding":{"logo_local":"","logo_url":"","favicon_local":"","favicon_url":""},
 "app":{"title":"Support Case Manager","subtitle":"Case Manager"},
 "irc":{"network":"","support_channels":[],"allowed_queries":[]},
 "profiles":{"allowed_hosts":[],"path_prefix":"profile"},
 "appearance":{"default_theme":"Light"},
 "case_manager":{"default_filter":"Active"}
}

def clean(v,limit=1000):
    v=re.sub(r"[\x00-\x1f\x7f]"," ",v).replace("|"," ").strip()
    return re.sub(r"\s+"," ",v)[:limit]
def valid_email(v): return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",v))
def valid_url(v): return bool(re.fullmatch(r"https?://[^\s]+",v,re.I))
def now(): return datetime.now().astimezone().isoformat(timespec="seconds")

class Store:
    STATUSES=("started","waiting","ready","submitted","closed")
    def __init__(self,path):
        legacy=Path(path); self.legacy=legacy; self.root=legacy.parent/"cases"; self.index_path=self.root/"index.json"; self.index={"version":2,"cases":[]}; self.root.mkdir(parents=True,exist_ok=True)
        for status in self.STATUSES:(self.root/status).mkdir(parents=True,exist_ok=True)
        self.load_index(); self.migrate_legacy()
    def load_index(self):
        if self.index_path.exists():
            try:
                d=json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(d,dict) and isinstance(d.get("cases"),list):self.index=d
            except Exception:self.rebuild_index()
        else:self.rebuild_index()
    def atomic_write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.stem+"_",suffix=".tmp",dir=path.parent,text=True)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(data,f,indent=2,ensure_ascii=False)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    def case_path(self,cid,status):
        folder=re.sub(r"[^a-z0-9]+","-",str(status or "started").lower()).strip("-") or "started"
        return self.root/folder/f"{cid}.json"
    def summary(self,c):return {k:c.get(k) for k in ("case_id","profile_name","status","created_at","updated_at","mode")}
    def save_index(self):self.atomic_write(self.index_path,self.index)
    def rebuild_index(self):
        by_id={}
        for status in self.STATUSES:
            for f in (self.root/status).glob("*.json"):
                try:
                    c=json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(c,dict) and c.get("case_id"): by_id[c["case_id"]]=self.summary(c)
                except Exception:pass
        self.index={"version":2,"cases":list(by_id.values())}; self.save_index(); return len(by_id)
    def import_cases_payload(self,data):
        count=0; cases=[]
        if isinstance(data,dict): cases=data.get("cases",[])
        elif isinstance(data,list): cases=data
        for c in cases:
            if isinstance(c,dict) and c.get("case_id"):
                if "status" not in c: c["status"]="Started"
                self.upsert(c); count+=1
        return count
    def migrate_legacy(self):
        marker=self.root/".migration_complete"
        if marker.exists() or not self.legacy.exists():return
        try:
            d=json.loads(self.legacy.read_text(encoding="utf-8")); self.import_cases_payload(d)
            backup=self.legacy.with_suffix(self.legacy.suffix+".migrated.bak")
            if not backup.exists():self.legacy.replace(backup)
            marker.write_text(datetime.now().astimezone().isoformat(timespec="seconds"),encoding="utf-8")
        except Exception:pass
    def recover(self):
        imported=0
        candidates=[self.legacy,self.legacy.with_suffix(self.legacy.suffix+".migrated.bak"),self.root/"cases.json",self.root.parent/"cases.json",self.root.parent/"cases.json.migrated.bak"]
        seen=set()
        for path in candidates:
            try: key=str(path.resolve())
            except OSError: key=str(path)
            if key in seen or not path.exists() or not path.is_file(): continue
            seen.add(key)
            try: imported+=self.import_cases_payload(json.loads(path.read_text(encoding="utf-8")))
            except Exception: pass
        for f in self.root.glob("*.json"):
            if f.name=="index.json": continue
            try:
                c=json.loads(f.read_text(encoding="utf-8"))
                if isinstance(c,dict) and c.get("case_id"):
                    self.upsert(c); imported+=1
                    try:f.unlink()
                    except OSError:pass
            except Exception:pass
        indexed=self.rebuild_index()
        return {"indexed":indexed,"imported":imported}
    def all(self):return list(self.index.get("cases",[]))
    def get(self,cid):
        meta=next((x for x in self.index.get("cases",[]) if x.get("case_id")==cid),None)
        if meta:
            f=self.case_path(cid,meta.get("status","Started"))
            if f.exists():
                try:return json.loads(f.read_text(encoding="utf-8"))
                except Exception:return None
        for status in self.STATUSES:
            f=self.root/status/f"{cid}.json"
            if f.exists():
                try:return json.loads(f.read_text(encoding="utf-8"))
                except Exception:return None
        return None
    def upsert(self,c):
        cid=c["case_id"]; status=c.get("status","Started"); old=next((x for x in self.index.get("cases",[]) if x.get("case_id")==cid),None)
        old_status=old.get("status") if old else None; new_path=self.case_path(cid,status); self.atomic_write(new_path,c)
        if old_status and str(old_status).lower()!=str(status).lower():
            old_path=self.case_path(cid,old_status)
            if old_path.exists() and old_path!=new_path:
                try:old_path.unlink()
                except OSError:pass
        for status_name in self.STATUSES:
            other=self.root/status_name/f"{cid}.json"
            if other.exists() and other!=new_path:
                try:other.unlink()
                except OSError:pass
        summary=self.summary(c)
        if old:old.clear(); old.update(summary)
        else:self.index.setdefault("cases",[]).append(summary)
        self.save_index()
    def delete(self,cid):
        removed=False
        for status in self.STATUSES:
            f=self.root/status/f"{cid}.json"
            if f.exists():
                try:f.unlink(); removed=True
                except OSError:pass
        before=len(self.index.get("cases",[]))
        self.index["cases"]=[x for x in self.index.get("cases",[]) if x.get("case_id")!=cid]
        if len(self.index["cases"])!=before: removed=True
        if removed:self.save_index()
        return removed

class ThemedModal:
    def __init__(self,app,title,message,kind="info"):
        self.app=app; c=THEMES[app.theme_name.get()]
        win=tk.Toplevel(app.root); self.win=win; win.title(title); win.transient(app.root); win.grab_set(); win.resizable(False,False); win.configure(bg=c["bg"]); win.attributes("-topmost",True)
        colors={"error":"#c92a3d","success":"#198754","info":c["accent"]}; accent=colors.get(kind,c["accent"]); icon=ICONS["error"] if kind=="error" else (ICONS["ok"] if kind=="success" else ICONS["info"])
        box=tk.Frame(win,bg=c["panel"],padx=18,pady=18); box.pack(fill="both",expand=True,padx=2,pady=2)
        tk.Label(box,text=icon,width=2,bg=accent,fg="#ffffff",font=("Segoe UI Semibold",15),padx=6,pady=4).grid(row=0,column=0,sticky="n",padx=(0,14))
        tk.Label(box,text=title,bg=c["panel"],fg=c["text"],font=("Segoe UI Semibold",12),anchor="w").grid(row=0,column=1,sticky="w")
        tk.Label(box,text=message,bg=c["panel"],fg=c["text"],font=("Segoe UI",10),justify="left",wraplength=390,anchor="w").grid(row=1,column=1,sticky="w",pady=(5,14))
        b=tk.Button(box,text="✓  OK",command=self.close,bg=accent,fg="#ffffff",activebackground=accent,activeforeground="#ffffff",relief="flat",padx=18,pady=6,font=("Segoe UI Semibold",9)); b.grid(row=2,column=1,sticky="e")
        win.bind("<Return>",lambda e:self.close()); win.bind("<Escape>",lambda e:self.close()); win.protocol("WM_DELETE_WINDOW",self.close); win.update_idletasks()
        x=app.root.winfo_rootx()+(app.root.winfo_width()-win.winfo_width())//2; y=app.root.winfo_rooty()+(app.root.winfo_height()-win.winfo_height())//2; win.geometry(f"+{max(x,0)}+{max(y,0)}"); b.focus_set()
    def close(self):
        try:self.win.grab_release()
        except tk.TclError:pass
        self.win.destroy()

class ToolTip:
    def __init__(self,widget,text):
        self.widget,self.text,self.tip=widget,text,None
        widget.bind("<Enter>",self.show,add="+"); widget.bind("<Leave>",self.hide,add="+")
    def show(self,_=None):
        if self.tip:return
        x=self.widget.winfo_rootx()+18; y=self.widget.winfo_rooty()+self.widget.winfo_height()+6
        self.tip=tk.Toplevel(self.widget); self.tip.wm_overrideredirect(True); self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip,text=self.text,justify="left",background="#fff7d6",foreground="#202124",relief="solid",borderwidth=1,padx=7,pady=4,font=("Segoe UI",9)).pack()
    def hide(self,_=None):
        if self.tip:self.tip.destroy(); self.tip=None

class App:
    def __init__(self,root,args):
        self.root,self.args=root,args; self.dirty=False; self.case_id=None; self.created_at=None; self.loading=False
        self.app_dir=Path(args.outdir).resolve().parent; self.config_path=self.app_dir/"config.json"
        scaffold_errors=self.ensure_scaffolding()
        if scaffold_errors: raise RuntimeError("Unable to prepare application folders: "+"; ".join(scaffold_errors))
        self.config=self.load_config(); self.store=Store(args.cases)
        root.title(self.config.get("app",{}).get("title","Support Case Manager")); self.setup_brand_icon(); root.geometry("1180x760"); root.minsize(960,640)
        self.theme_name=tk.StringVar(value=self.config.get("appearance",{}).get("default_theme","Light")); self.style=ttk.Style(root); self.style.theme_use("clam")
        outer=ttk.Frame(root,padding=18); outer.pack(fill="both",expand=True)
        left=ttk.Frame(outer,style="Card.TFrame",padding=14); left.pack(side="left",fill="y",padx=(0,18))
        scroll_host=ttk.Frame(outer); scroll_host.pack(side="left",fill="both",expand=True)
        self.main_canvas=tk.Canvas(scroll_host,highlightthickness=0,borderwidth=0)
        self.main_scroll=ttk.Scrollbar(scroll_host,orient="vertical",command=self.main_canvas.yview)
        self.main_canvas.configure(yscrollcommand=self.main_scroll.set)
        self.main_scroll.pack(side="right",fill="y",padx=(12,0)); self.main_canvas.pack(side="top",fill="both",expand=True)
        right=ttk.Frame(self.main_canvas); self.main_window=self.main_canvas.create_window((0,0),window=right,anchor="nw")
        self.main_header=ttk.Frame(right); self.main_header.pack(fill="x",pady=(0,14)); ttk.Label(self.main_header,text=self.config.get("app",{}).get("title","Support Case Manager"),style="Title.TLabel").pack(side="left")
        right.bind("<Configure>",lambda e:self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all")))
        self.main_canvas.bind("<Configure>",lambda e:self.main_canvas.itemconfigure(self.main_window,width=e.width))
        def _wheel(e): self.main_canvas.yview_scroll(int(-1*(e.delta/120)),"units")
        self.main_canvas.bind("<Enter>",lambda e:self.main_canvas.bind_all("<MouseWheel>",_wheel))
        self.main_canvas.bind("<Leave>",lambda e:self.main_canvas.unbind_all("<MouseWheel>"))
        self.logo_label=tk.Label(left,borderwidth=0,highlightthickness=0)
        self.logo_label.pack(anchor="w",pady=(0,8))
        self.setup_brand_logo()
        self.sidebar_subtitle=tk.StringVar(value=self.config.get("app",{}).get("subtitle","Case Manager"))
        ttk.Label(left,textvariable=self.sidebar_subtitle,style="Brand.TLabel").pack(anchor="w",pady=(0,12))
        self.btn_settings=ttk.Button(left,text="⚙  Settings    Ctrl+,",command=self.open_settings); self.btn_settings.pack(fill="x",pady=(0,10))
        ttk.Label(left,text="◐   Appearance",style="Subtle.TLabel").pack(anchor="w",pady=(2,2))
        self.theme_toggle=ttk.Button(left,command=self.toggle_theme,style="ThemeToggle.TButton"); self.theme_toggle.pack(fill="x",pady=(5,2))
        ttk.Label(left,text="≡   Status filter",style="Subtle.TLabel").pack(anchor="w",pady=(8,2))
        self.status_filter=tk.StringVar(value=self.config.get("case_manager",{}).get("default_filter","Active"))
        sf=ttk.Combobox(left,textvariable=self.status_filter,values=("Active","All")+STATUSES,state="readonly",width=31)
        sf.pack(fill="x",pady=(5,2)); sf.bind("<<ComboboxSelected>>",lambda e:self.refresh_cases())
        ttk.Label(left,text="⌕   Search",style="Subtle.TLabel").pack(anchor="w",pady=(8,2))
        self.search=tk.StringVar(); se=ttk.Entry(left,textvariable=self.search,width=32); se.pack(fill="x",pady=(5,5)); se.bind("<KeyRelease>",lambda e:self.refresh_cases())
        case_actions=ttk.Frame(left,style="Card.TFrame"); case_actions.pack(side="bottom",fill="x",pady=(8,0))
        self.btn_rescan=ttk.Button(case_actions,text="↻  Rescan / Recover",command=self.rescan_cases); self.btn_rescan.pack(fill="x",pady=(0,6))
        self.btn_delete=ttk.Button(case_actions,text="✕  Delete Case    Del",command=self.delete_case); self.btn_delete.pack(fill="x")
        ToolTip(self.btn_rescan,"Rebuild the case list from files on disk and import any legacy cases.json backups")
        ToolTip(self.btn_delete,"Permanently delete the selected case (Delete)")
        case_list_frame=ttk.Frame(left,style="Card.TFrame"); case_list_frame.pack(fill="both",expand=True)
        self.list=tk.Listbox(case_list_frame,width=36,height=8)
        case_scroll=ttk.Scrollbar(case_list_frame,orient="vertical",command=self.list.yview); self.list.configure(yscrollcommand=case_scroll.set)
        case_scroll.pack(side="right",fill="y",padx=(6,0)); self.list.pack(side="left",fill="both",expand=True); self.list.bind("<<ListboxSelect>>",self.open_selected)
        self.profile_name=tk.StringVar(); self.status=tk.StringVar(value="Started"); self.mode=tk.StringVar(value="Basic"); self.email=tk.StringVar(); self.profile=tk.StringVar(); self.speed=tk.StringVar(); self.main_original={}; self.main_field_widgets={}; self.main_tracking_ready=False
        summary=ttk.LabelFrame(right,text="  ◈  Case Summary  ",style="Card.TLabelframe",padding=12); summary.pack(fill="x",pady=(0,14))
        summary_top=ttk.Frame(summary,style="Card.TFrame"); summary_top.pack(fill="x")
        self.summary_name=tk.StringVar(value="New Case")
        ttk.Label(summary_top,textvariable=self.summary_name,style="SummaryName.TLabel").pack(side="left")
        self.summary_badge=tk.Label(summary_top,text=" STARTED ",font=("Segoe UI Semibold",9),padx=10,pady=4,borderwidth=0); self.summary_badge.pack(side="right")
        summary_meta=ttk.Frame(summary,style="Card.TFrame"); summary_meta.pack(fill="x",pady=(8,0))
        self.summary_created=tk.StringVar(value="Created: —"); self.summary_updated=tk.StringVar(value="Last update: —")
        ttk.Label(summary_meta,textvariable=self.summary_created,style="CardMuted.TLabel").pack(side="left")
        ttk.Label(summary_meta,textvariable=self.summary_updated,style="CardMuted.TLabel").pack(side="right")
        mode_card=ttk.LabelFrame(right,text="  ◉  Support Mode  ",style="Card.TLabelframe",padding=12); mode_card.pack(fill="x",pady=(0,14))
        mode_row=ttk.Frame(mode_card,style="Card.TFrame"); mode_row.pack(fill="x")
        self.mode_radios=[]
        for label in MODE_LABELS:
            rb=ttk.Radiobutton(mode_row,text=label,value=label,variable=self.mode,style="Mode.TRadiobutton"); rb.pack(side="left",padx=(0,22),pady=2); self.mode_radios.append(rb)
        account=ttk.LabelFrame(right,text="  ▤  Account & Case Details  ",style="Card.TLabelframe",padding=14); account.pack(fill="x",pady=(0,14))
        form=ttk.Frame(account,style="Card.TFrame"); form.pack(fill="x")
        rows=(("Profile name",self.profile_name,"profile_name"),("Status",self.status,"status"),("Email",self.email,"email"),("Profile URL",self.profile,"profile"),("Speed Test URL",self.speed,"speed"))
        for i,(label,var,key) in enumerate(rows):
            lab=ttk.Label(form,text=label); lab.grid(row=i,column=0,sticky="w",pady=4,padx=(0,8))
            if label=="Status":
                w=ttk.Combobox(form,textvariable=var,values=STATUSES,state="readonly"); w.grid(row=i,column=1,sticky="ew",pady=4); border=None
            else:
                border=tk.Frame(form,bg=THEMES[self.theme_name.get()]["muted"],padx=2,pady=2); border.grid(row=i,column=1,sticky="ew",pady=4)
                w=tk.Entry(border,textvariable=var,bg=THEMES[self.theme_name.get()]["field"],fg=THEMES[self.theme_name.get()]["text"],insertbackground=THEMES[self.theme_name.get()]["text"],relief="flat",bd=0,font=("Segoe UI",10)); w.pack(fill="x",ipady=5)
            self.main_field_widgets[key]=(lab,w,border)
        form.columnconfigure(1,weight=1)
        msg_card=ttk.LabelFrame(right,text="  ▣  Message Preview  ",style="Card.TLabelframe",padding=12); msg_card.pack(fill="both",expand=True,pady=(0,14)); self.message_border=tk.Frame(msg_card,bg=THEMES[self.theme_name.get()]["muted"],padx=2,pady=2); self.message_border.pack(fill="both",expand=True); self.message=tk.Text(self.message_border,height=7,wrap="word",padx=10,pady=8); self.message.pack(fill="both",expand=True)
        notes_card=ttk.LabelFrame(right,text="  ✎  Case Notes  ",style="Card.TLabelframe",padding=12); notes_card.pack(fill="x",pady=(0,14)); self.notes_border=tk.Frame(notes_card,bg=THEMES[self.theme_name.get()]["muted"],padx=2,pady=2); self.notes_border.pack(fill="x"); self.notes=tk.Text(self.notes_border,height=5,wrap="word",padx=10,pady=8); self.notes.pack(fill="x")
        self.info=tk.StringVar(value="Create a case or select one to resume it.")
        footer_shell=ttk.Frame(scroll_host,style="ActionShell.TFrame",padding=(1,1,1,1)); footer_shell.pack(side="bottom",fill="x",pady=(10,0))
        footer=ttk.Frame(footer_shell,style="ActionBar.TFrame",padding=(14,11)); footer.pack(fill="x")
        self.dirty_text=tk.StringVar(value="✓ SAVED"); self.dirty_label=ttk.Label(footer,textvariable=self.dirty_text,style="Saved.TLabel"); self.dirty_label.pack(side="left",padx=(0,14))
        ttk.Label(footer,text="ACTIONS",style="ActionLabel.TLabel").pack(side="left",padx=(0,14))
        self.btn_new=ttk.Button(footer,text="＋  New Case    Ctrl+N",command=self.new_case); self.btn_new.pack(side="left",padx=(0,6))
        self.btn_rebuild=ttk.Button(footer,text="↻  Rebuild    Ctrl+R",command=self.rebuild); self.btn_rebuild.pack(side="left",padx=6)
        self.btn_save=ttk.Button(footer,text="▣  Save Case    Ctrl+S",command=self.save_case); self.btn_save.pack(side="left",padx=6)
        self.btn_send=ttk.Button(footer,text="➜  Send to mIRC    Ctrl+Enter",command=self.send,style="Accent.TButton"); self.btn_send.pack(side="right")
        ToolTip(self.btn_new,"Start a new blank case (Ctrl+N)")
        ToolTip(self.btn_rebuild,"Rebuild the message from the current mode and account fields (Ctrl+R)")
        ToolTip(self.btn_save,"Save this case to its current status folder (Ctrl+S)")
        ToolTip(self.btn_send,"Save this case as Submitted, then hand the message to mIRC (Ctrl+Enter)")
        for v in (self.email,self.profile,self.speed): v.trace_add("write",lambda *_: self.auto_rebuild())
        self.mode.trace_add("write",lambda *_: self.auto_rebuild())
        self.profile_name.trace_add("write",lambda *_:self.update_summary()); self.status.trace_add("write",lambda *_:self.update_summary())
        for v in (self.profile_name,self.status,self.mode,self.email,self.profile,self.speed): v.trace_add("write",self.refresh_main_tracking)
        self.profile.trace_add("write",self.profile_changed)
        self.notes.bind("<<Modified>>",self.main_text_modified,add="+"); self.message.bind("<<Modified>>",self.main_text_modified,add="+")
        self.notes.edit_modified(False); self.message.edit_modified(False)
        ToolTip(self.btn_settings,"⚙ Settings\nEdit application, IRC, branding, appearance, and profile URL settings.\nKeyboard: Ctrl+, ")
        ToolTip(self.theme_toggle,"◐ Toggle appearance\nSwitch between Light and Dark. The choice is saved immediately.")
        ToolTip(sf,"Filter the case list by status")
        ToolTip(self.list,"Select a saved case to open it")
        self.root.bind("<Control-n>",lambda e:self.new_case())
        self.root.bind("<Control-N>",lambda e:self.new_case())
        self.root.bind("<Control-r>",lambda e:self.rebuild())
        self.root.bind("<Control-R>",lambda e:self.rebuild())
        self.root.bind("<Control-s>",lambda e:self.save_case())
        self.root.bind("<Control-S>",lambda e:self.save_case())
        self.root.bind("<Control-Return>",lambda e:self.send())
        self.root.bind("<Control-comma>",lambda e:self.open_settings())
        self.root.bind("<Delete>",self.delete_key)
        self.root.bind("<Escape>",lambda e:self.root.focus())
        self.apply_theme(); self.rescan_cases(quiet=True); self.new_case(); self.update_summary()
    def delete_key(self,event=None):
        w=self.root.focus_get()
        if isinstance(w,(tk.Entry,tk.Text,ttk.Entry,ttk.Combobox)): return
        self.delete_case()
    def profile_name_from_url(self,value):
        try:
            u=urlparse(value.strip()); host=(u.hostname or "").lower(); pcfg=self.config.get("profiles",{})
            hosts=[str(x).lower() for x in pcfg.get("allowed_hosts",[])]
            if hosts and host not in hosts:return ""
            prefix=str(pcfg.get("path_prefix","profile")).strip("/").lower()
            parts=[unquote(x) for x in u.path.split("/") if x]
            if len(parts)>=2 and parts[0].lower()==prefix:return clean(parts[1],120)
        except ValueError:pass
        return ""
    def profile_changed(self,*_):
        if self.loading:return
        if not self.profile_name.get().strip():
            name=self.profile_name_from_url(self.profile.get())
            if name:self.profile_name.set(name)
    def save_config(self,newcfg,win):
        self.config=newcfg
        try:self.config_path.write_text(json.dumps(newcfg,indent=2),encoding="utf-8")
        except Exception as e:return self.modal("Settings error",f"Unable to save config.json. {e}","error")
        self.sidebar_subtitle.set(newcfg.get("app",{}).get("subtitle","Case Manager")); self.status_filter.set(newcfg.get("case_manager",{}).get("default_filter","Active")); self.refresh_cases()
        theme=newcfg.get("appearance",{}).get("default_theme","Light")
        if theme in THEMES:self.theme_name.set(theme); self.apply_theme()
        win.destroy(); self.modal("Settings saved","Settings were saved successfully. Branding path changes take effect on the next launch.","success")
    def confirm_action(self,parent,title,message,confirm_label,on_confirm,accent="#d99100"):
        c=THEMES[self.theme_name.get()]; win=tk.Toplevel(parent); win.title(title); win.transient(parent); win.grab_set(); win.resizable(False,False); win.configure(bg=c["bg"])
        box=tk.Frame(win,bg=c["panel"],padx=18,pady=18); box.pack(fill="both",expand=True,padx=2,pady=2)
        tk.Label(box,text="!",width=2,bg=accent,fg="#ffffff",font=("Segoe UI Semibold",15),padx=6,pady=4).grid(row=0,column=0,sticky="n",padx=(0,14))
        tk.Label(box,text=title,bg=c["panel"],fg=c["text"],font=("Segoe UI Semibold",12)).grid(row=0,column=1,sticky="w")
        tk.Label(box,text=message,bg=c["panel"],fg=c["text"],font=("Segoe UI",10),justify="left",wraplength=390).grid(row=1,column=1,sticky="w",pady=(5,14))
        bar=tk.Frame(box,bg=c["panel"]); bar.grid(row=2,column=1,sticky="e")
        def cancel(): 
            try:win.grab_release()
            except tk.TclError:pass
            win.destroy()
        def accept(): cancel(); on_confirm()
        tk.Button(bar,text="Cancel",command=cancel,padx=14,pady=6).pack(side="left",padx=5); tk.Button(bar,text=confirm_label,command=accept,bg=accent,fg="#fff",relief="flat",padx=14,pady=6).pack(side="left",padx=5)
        win.bind("<Escape>",lambda e:cancel()); win.bind("<Return>",lambda e:accept()); win.protocol("WM_DELETE_WINDOW",cancel); win.update_idletasks(); x=parent.winfo_rootx()+(parent.winfo_width()-win.winfo_width())//2; y=parent.winfo_rooty()+(parent.winfo_height()-win.winfo_height())//2; win.geometry(f"+{max(x,0)}+{max(y,0)}")
    def confirm_discard_settings(self,parent,on_discard):
        self.confirm_action(parent,"Unsaved settings","Settings have changed. Discard the pending changes?","Discard",on_discard)
    def open_settings(self):
        c=THEMES[self.theme_name.get()]; win=tk.Toplevel(self.root); win.title("Settings"); win.transient(self.root); win.grab_set(); win.geometry("680x650"); win.minsize(560,460); win.configure(bg=c["bg"])
        shell=tk.Frame(win,bg=c["bg"]); shell.pack(fill="both",expand=True,padx=12,pady=12)
        footer=tk.Frame(shell,bg=c["panel"],highlightthickness=1,highlightbackground=c["accent"],padx=14,pady=11); footer.pack(side="bottom",fill="x",pady=(10,0))
        host=tk.Frame(shell,bg=c["bg"]); host.pack(side="top",fill="both",expand=True)
        canvas=tk.Canvas(host,bg=c["bg"],highlightthickness=0,borderwidth=0); scroll=ttk.Scrollbar(host,orient="vertical",command=canvas.yview); canvas.configure(yscrollcommand=scroll.set); scroll.pack(side="right",fill="y",padx=(10,0)); canvas.pack(side="left",fill="both",expand=True)
        body=tk.Frame(canvas,bg=c["panel"],padx=18,pady=16); wid=canvas.create_window((0,0),window=body,anchor="nw"); body.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all"))); canvas.bind("<Configure>",lambda e:canvas.itemconfigure(wid,width=e.width))
        def wheel(e): canvas.yview_scroll(int(-1*(e.delta/120)),"units")
        canvas.bind("<Enter>",lambda e:canvas.bind_all("<MouseWheel>",wheel)); canvas.bind("<Leave>",lambda e:canvas.unbind_all("<MouseWheel>"))
        tk.Label(body,text="⚙  Settings",bg=c["panel"],fg=c["text"],font=("Segoe UI Semibold",20),anchor="w").grid(row=0,column=0,columnspan=2,sticky="w",pady=(0,14)); self.settings_change_note=tk.Label(body,text="No pending changes",bg=c["panel"],fg=c["muted"],font=("Segoe UI",9),anchor="e"); self.settings_change_note.grid(row=0,column=1,sticky="e")
        cfg=json.loads(json.dumps(self.config)); entries={}; widgets={}; originals={}
        fields=[("Application title","app.title"),("Sidebar subtitle","app.subtitle"),("IRC network","irc.network"),("Support channels (comma separated)","irc.support_channels"),("Allowed queries (comma separated)","irc.allowed_queries"),("Profile hosts (comma separated)","profiles.allowed_hosts"),("Profile path prefix","profiles.path_prefix"),("Local logo path","branding.logo_local"),("Remote logo URL","branding.logo_url"),("Local favicon path","branding.favicon_local"),("Remote favicon URL","branding.favicon_url")]
        def getv(path):
            a,b=path.split("."); v=cfg.get(a,{}).get(b,""); return ", ".join(v) if isinstance(v,list) else str(v)
        for i,(label,path) in enumerate(fields,start=1):
            row=tk.Frame(body,bg=c["panel"]); row.grid(row=i,column=0,columnspan=2,sticky="ew",pady=4); row.columnconfigure(1,weight=1)
            lab=tk.Label(row,text="✎  "+label,bg=c["panel"],fg=c["text"],anchor="w",font=("Segoe UI",9)); lab.grid(row=0,column=0,sticky="w",padx=(0,8))
            border=tk.Frame(row,bg=c["muted"],padx=2,pady=2); border.grid(row=0,column=1,sticky="ew")
            var=tk.StringVar(); var.set(getv(path)); originals[path]=var.get(); ent=tk.Entry(border,textvariable=var,bg=c["field"],fg=c["text"],insertbackground=c["text"],relief="flat",bd=0,font=("Segoe UI",10)); ent.pack(fill="x",ipady=5); entries[path]=var; widgets[path]=(border,lab); ToolTip(ent,"✎ Text input\n"+label)
        r=len(fields)+1
        theme_label=tk.Label(body,text="◐  Appearance",bg=c["panel"],fg=c["text"],anchor="w"); theme_label.grid(row=r,column=0,sticky="w",pady=4); theme=tk.StringVar(); theme.set(cfg.get("appearance",{}).get("default_theme","Light")); originals["appearance.default_theme"]=theme.get()
        def refresh_settings_theme_button(): settings_theme_btn.configure(text="☀  Light     ◉────○     Dark" if theme.get()=="Light" else "Light     ○────◉     ☾  Dark")
        def toggle_settings_theme(): theme.set("Dark" if theme.get()=="Light" else "Light"); refresh_settings_theme_button()
        settings_theme_btn=ttk.Button(body,command=toggle_settings_theme,style="ThemeToggle.TButton"); settings_theme_btn.grid(row=r,column=1,sticky="ew",pady=4); refresh_settings_theme_button(); ToolTip(settings_theme_btn,"◐ Toggle default appearance")
        r+=1
        filter_label=tk.Label(body,text="≡  Default case filter",bg=c["panel"],fg=c["text"],anchor="w"); filter_label.grid(row=r,column=0,sticky="w",pady=6); filt=tk.StringVar(); filt.set(cfg.get("case_manager",{}).get("default_filter","Active")); originals["case_manager.default_filter"]=filt.get(); filter_box=ttk.Combobox(body,textvariable=filt,values=("Active","All")+STATUSES,state="readonly"); filter_box.grid(row=r,column=1,sticky="ew",pady=4); body.columnconfigure(1,weight=1)
        def changed_map():
            changes={path:(var.get()!=originals[path]) for path,var in entries.items()}; changes["appearance.default_theme"]=theme.get()!=originals["appearance.default_theme"]; changes["case_manager.default_filter"]=filt.get()!=originals["case_manager.default_filter"]; return changes
        def refresh_tracking(*_):
            changes=changed_map(); count=sum(changes.values())
            for path,(border,lab) in widgets.items():
                changed=changes[path]; border.configure(bg="#d99100" if changed else c["muted"]); lab.configure(fg="#d99100" if changed else c["text"])
            theme_label.configure(fg="#d99100" if changes["appearance.default_theme"] else c["text"]); filter_label.configure(fg="#d99100" if changes["case_manager.default_filter"] else c["text"])
            self.settings_change_note.configure(text=(f"● {count} pending change"+("s" if count!=1 else "")) if count else "✓ No pending changes",fg="#d99100" if count else "#198754")
        for var in entries.values(): var.trace_add("write",refresh_tracking)
        theme.trace_add("write",refresh_tracking); filt.trace_add("write",refresh_tracking); refresh_tracking()
        def has_pending_changes(): return any(changed_map().values())
        def close_settings():
            try: win.unbind_all("<Escape>")
            except tk.TclError: pass
            win.destroy()
        def cancel_settings(event=None):
            if has_pending_changes(): self.confirm_discard_settings(win,close_settings)
            else: close_settings()
        def commit():
            for path,var in entries.items():
                a,b=path.split("."); value=var.get().strip(); cfg.setdefault(a,{})
                if path in ("irc.support_channels","irc.allowed_queries","profiles.allowed_hosts"): value=[x.strip() for x in value.split(",") if x.strip()]
                cfg[a][b]=value
            cfg.setdefault("appearance",{})["default_theme"]=theme.get(); cfg.setdefault("case_manager",{})["default_filter"]=filt.get(); win.unbind_all("<Escape>"); self.save_config(cfg,win)
        buttons=tk.Frame(footer,bg=c["panel"]); buttons.pack(anchor="center"); cancel_btn=tk.Button(buttons,text="✕  Cancel   Esc",command=cancel_settings,padx=18,pady=7); cancel_btn.pack(side="left",padx=6); save_btn=tk.Button(buttons,text="✓  Save Settings   Ctrl+S",command=commit,bg=c["accent"],fg="#fff",relief="flat",padx=20,pady=7); save_btn.pack(side="left",padx=6)
        ToolTip(cancel_btn,"✕ Cancel changes\nKeyboard: Esc"); ToolTip(save_btn,"✓ Save configuration to config.json\nKeyboard: Ctrl+S")
        win.bind_all("<Escape>",cancel_settings); win.protocol("WM_DELETE_WINDOW",cancel_settings); win.bind("<Control-s>",lambda e:commit()); win.bind("<Control-S>",lambda e:commit())
    def ensure_scaffolding(self):
        problems=[]
        try:
            self.app_dir.mkdir(parents=True,exist_ok=True)
            Path(self.args.outdir).mkdir(parents=True,exist_ok=True)
            cases_root=self.app_dir/"cases"; cases_root.mkdir(parents=True,exist_ok=True)
            for status in ("started","waiting","ready","submitted","closed"):(cases_root/status).mkdir(parents=True,exist_ok=True)
            assets=self.app_dir/"assets"; assets.mkdir(parents=True,exist_ok=True)
        except OSError as e: problems.append(str(e))
        return problems
    def load_config(self):
        cfg=json.loads(json.dumps(DEFAULT_CONFIG))
        if self.config_path.exists():
            try:
                user=json.loads(self.config_path.read_text(encoding="utf-8"))
                for section,values in user.items():
                    if isinstance(values,dict) and isinstance(cfg.get(section),dict):cfg[section].update(values)
            except Exception:pass
        else:
            try:self.config_path.write_text(json.dumps(cfg,indent=2),encoding="utf-8")
            except Exception:pass
        return cfg
    def asset_path(self,key):
        v=self.config.get("branding",{}).get(key,""); return self.app_dir/Path(v) if v else None
    def cache_remote(self,url,dest,max_bytes):
        if not url or not dest:return False
        try:
            dest.parent.mkdir(parents=True,exist_ok=True)
            with urlopen(url,timeout=4) as r:data=r.read(max_bytes+1)
            if not data or len(data)>max_bytes:return False
            fd,tmp=tempfile.mkstemp(prefix="brand_",suffix=".tmp",dir=dest.parent)
            try:
                with os.fdopen(fd,"wb") as f:f.write(data)
                os.replace(tmp,dest)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
            return True
        except Exception:return False
    def setup_brand_icon(self):
        local=self.asset_path("favicon_local"); url=self.config.get("branding",{}).get("favicon_url","")
        if local and not local.exists():self.cache_remote(url,local,262144)
        if local and local.exists():
            try:self.root.iconbitmap(default=str(local))
            except Exception:pass
    def setup_brand_logo(self):
        local=self.asset_path("logo_local"); url=self.config.get("branding",{}).get("logo_url","")
        if local and not local.exists():self.cache_remote(url,local,2097152)
        if local and local.exists():
            try:
                img=tk.PhotoImage(file=str(local)); maxw=220
                if img.width()>maxw:img=img.subsample(max(1,(img.width()+maxw-1)//maxw))
                self.brand_logo=img; self.logo_label.configure(image=img)
                return
            except Exception:pass
        self.logo_label.configure(text="SC",font=("Segoe UI Semibold",20),fg="#667085")
    def mark_dirty(self):
        if getattr(self,"loading",False):return
        self.dirty=True
        if hasattr(self,"dirty_text"):
            self.dirty_text.set("● UNSAVED"); self.dirty_label.configure(style="Dirty.TLabel")
    def mark_saved(self):
        self.dirty=False
        if hasattr(self,"dirty_text"):
            self.dirty_text.set("✓ SAVED"); self.dirty_label.configure(style="Saved.TLabel")
    def text_modified(self,event):
        w=event.widget
        if w.edit_modified():
            self.mark_dirty(); w.edit_modified(False)
    def pretty_time(self,value):
        if not value:return "—"
        try:return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y  %I:%M %p")
        except (ValueError,TypeError):return str(value)
    def update_summary(self):
        self.summary_name.set(self.profile_name.get().strip() or "New Case")
        status=self.status.get() or "Started"; colors={"Started":"#337ab7","Waiting":"#d99100","Ready":"#198754","Submitted":"#7950b3","Closed":"#6c757d"}
        self.summary_badge.configure(text=f" {status.upper()} ",bg=colors.get(status,"#6c757d"),fg="#ffffff")
        self.summary_created.set("Created: "+self.pretty_time(self.created_at))
        current=self.store.get(self.case_id) if self.case_id else None
        updated=current.get("updated_at") if current else None
        self.summary_updated.set("Last update: "+self.pretty_time(updated))

    def modal(self,title,message,kind="info"):
        ThemedModal(self,title,message,kind)

    def save_theme_preference(self):
        self.config.setdefault("appearance",{})["default_theme"]=self.theme_name.get()
        try:self.config_path.write_text(json.dumps(self.config,indent=2),encoding="utf-8")
        except Exception as e:self.modal("Theme setting",f"Theme changed for this session, but config.json could not be updated: {e}","error")
    def toggle_theme(self):
        self.theme_name.set("Dark" if self.theme_name.get()=="Light" else "Light")
        self.apply_theme(); self.save_theme_preference()
    def apply_theme(self):
        c=THEMES[self.theme_name.get()]
        if hasattr(self,"theme_toggle"):
            self.theme_toggle.configure(text="☀  Light     ◉────○     Dark" if self.theme_name.get()=="Light" else "Light     ○────◉     ☾  Dark")
        self.root.configure(bg=c["bg"]); self.main_canvas.configure(bg=c["bg"]); 
        if hasattr(self,"logo_label"):
            self.logo_label.configure(bg="#111111" if self.theme_name.get()=="Light" else c["panel"])
        self.style.configure(".",background=c["bg"],foreground=c["text"],font=("Segoe UI",10))
        self.style.configure("TFrame",background=c["bg"]); self.style.configure("TLabel",background=c["bg"],foreground=c["text"]); self.style.configure("TButton",padding=(11,7),background=c["panel"],foreground=c["text"]); self.style.configure("ThemeToggle.TButton",padding=(12,7),background=c["field"],foreground=c["text"],font=("Segoe UI Semibold",9)); self.style.map("TButton",background=[("active",c["accent"])],foreground=[("active","#ffffff")])
        self.style.configure("Saved.TLabel",background=c["panel"],foreground="#198754",font=("Segoe UI Semibold",8)); self.style.configure("Dirty.TLabel",background=c["panel"],foreground="#d99100",font=("Segoe UI Semibold",8)); self.style.configure("ActionShell.TFrame",background=c["accent"]); self.style.configure("ActionBar.TFrame",background=c["panel"]); self.style.configure("ActionLabel.TLabel",background=c["panel"],foreground=c["muted"],font=("Segoe UI Semibold",8)); self.style.configure("Card.TFrame",background=c["panel"]); self.style.configure("Card.TLabelframe",background=c["panel"],foreground=c["text"],borderwidth=1,relief="solid"); self.style.configure("Card.TLabelframe.Label",background=c["panel"],foreground=c["text"],font=("Segoe UI Semibold",10),padding=(2,2))
        self.style.configure("SummaryName.TLabel",background=c["panel"],foreground=c["text"],font=("Segoe UI Semibold",15)); self.style.configure("CardMuted.TLabel",background=c["panel"],foreground=c["muted"],font=("Segoe UI",9)); self.style.configure("Brand.TLabel",background=c["panel"],foreground=c["accent"],font=("Segoe UI Semibold",17)); self.style.configure("Subtle.TLabel",background=c["panel"],foreground=c["muted"],font=("Segoe UI",9)); self.style.configure("Title.TLabel",background=c["bg"],foreground=c["text"],font=("Segoe UI Semibold",20))
        self.style.configure("Accent.TButton",background=c["accent"],foreground="#ffffff",padding=(17,8),font=("Segoe UI Semibold",10)); self.style.map("Accent.TButton",background=[("active",c["accent"])],foreground=[("active","#ffffff")])
        self.style.configure("Mode.TRadiobutton",background=c["panel"],foreground=c["text"],font=("Segoe UI Semibold",10),padding=(7,5)); self.style.map("Mode.TRadiobutton",background=[("active",c["panel"])],foreground=[("active",c["accent"]),("selected",c["accent"])])
        self.style.configure("ChangedMode.TRadiobutton",background=c["panel"],foreground="#d99100",font=("Segoe UI Semibold",10),padding=(7,5))
        self.style.configure("TEntry",fieldbackground=c["field"],foreground=c["text"]); self.style.configure("TCombobox",fieldbackground=c["field"],background=c["field"],foreground=c["text"],arrowcolor=c["text"]); self.style.map("TCombobox",fieldbackground=[("readonly",c["field"])],foreground=[("readonly",c["text"])])
        self.list.configure(bg=c["field"],fg=c["text"],selectbackground=("#30353d" if self.theme_name.get()=="Dark" else "#e8edf3"),selectforeground=c["text"],activestyle="none",relief="flat",highlightthickness=1,highlightbackground=c["panel"],highlightcolor=c["panel"]); [w.configure(bg=c["field"],fg=c["text"],insertbackground=c["text"],selectbackground=c["accent"],selectforeground="#ffffff",relief="flat") for w in (self.notes,self.message)]
        if hasattr(self,"main_field_widgets"):
            [w.configure(bg=c["field"],fg=c["text"],insertbackground=c["text"]) for _,w,b in self.main_field_widgets.values() if b]
        if hasattr(self,"main_tracking_ready") and self.main_tracking_ready:self.refresh_main_tracking()
    def main_snapshot(self):
        return {"profile_name":self.profile_name.get(),"status":self.status.get(),"mode":self.mode.get(),"email":self.email.get(),"profile":self.profile.get(),"speed":self.speed.get(),"notes":self.notes.get("1.0","end-1c"),"message":self.message.get("1.0","end-1c")}
    def set_main_baseline(self,saved=True):
        self.main_original=self.main_snapshot(); self.main_tracking_ready=True; self.refresh_main_tracking()
        if saved:self.mark_saved()
    def refresh_main_tracking(self,*_):
        if not getattr(self,"main_tracking_ready",False) or getattr(self,"loading",False):return
        snap=self.main_snapshot(); changed={k:(snap.get(k)!=self.main_original.get(k)) for k in snap}; count=sum(changed.values())
        c=THEMES[self.theme_name.get()]
        for key,(lab,w,border) in self.main_field_widgets.items():
            is_changed=changed.get(key,False); lab.configure(foreground="#d99100" if is_changed else c["text"]); border.configure(bg="#d99100" if is_changed else c["muted"]) if border else None
        self.notes_border.configure(bg="#d99100" if changed.get("notes") else c["muted"]); self.message_border.configure(bg="#d99100" if changed.get("message") else c["muted"])
        mode_changed=changed.get("mode",False)
        for rb in getattr(self,"mode_radios",[]): rb.configure(style="ChangedMode.TRadiobutton" if mode_changed else "Mode.TRadiobutton")
        self.dirty=count>0
        if hasattr(self,"dirty_text"):
            self.dirty_text.set((f"● {count} UNSAVED CHANGE"+("S" if count!=1 else "")) if count else "✓ SAVED"); self.dirty_label.configure(style="Dirty.TLabel" if count else "Saved.TLabel")
    def main_text_modified(self,event):
        w=event.widget
        if w.edit_modified(): w.edit_modified(False); self.refresh_main_tracking()
    def auto_rebuild(self):
        if not self.loading: self.rebuild()
    def rebuild(self):
        mode=MODE_LABELS.get(self.mode.get(),"basic")
        vals={"email":clean(self.email.get(),160) or "[EMAIL]","profile":clean(self.profile.get(),350) or "[PROFILE URL]","speedtest":clean(self.speed.get(),350) or "[SPEED TEST URL]"}
        self.message.delete("1.0","end"); self.message.insert("1.0",TEMPLATES[mode].format(**vals))
    def new_case(self):
        self.loading=True; self.case_id=uuid.uuid4().hex; self.created_at=now()
        for v,val in ((self.profile_name,""),(self.status,"Started"),(self.mode,"Basic"),(self.email,""),(self.profile,""),(self.speed,"")): v.set(val)
        self.notes.delete("1.0","end"); self.message.delete("1.0","end"); self.loading=False; self.rebuild(); self.info.set("New unsaved case"); self.set_main_baseline(saved=False); self.main_original["__new_case__"]=False; self.dirty=True; self.dirty_text.set("● NEW UNSAVED CASE"); self.dirty_label.configure(style="Dirty.TLabel")
    def record(self):
        return {"case_id":self.case_id,"profile_name":clean(self.profile_name.get(),120),"status":self.status.get(),"mode":MODE_LABELS.get(self.mode.get(),"basic"),"email":clean(self.email.get(),160),"profile_url":clean(self.profile.get(),350),"speedtest_url":clean(self.speed.get(),350),"notes":self.notes.get("1.0","end-1c").strip()[:5000],"message":clean(self.message.get("1.0","end-1c"),430),"created_at":self.created_at or now(),"updated_at":now()}
    def save_case(self,quiet=False):
        c=self.record()
        if not c["profile_name"]:
            self.modal("Validation error","Profile name is required before saving.","error"); return False
        self.store.upsert(c); self.refresh_cases(); self.set_main_baseline(saved=True); self.info.set(f"Saved: {c['profile_name']} [{c['status']}]"); self.update_summary()
        if not quiet: self.modal("Saved","Case saved.","success")
        return True
    def refresh_cases(self):
        q=self.search.get().lower().strip() if hasattr(self,"search") else ""; self.visible=[]; self.list.delete(0,"end")
        for c in sorted(self.store.all(),key=lambda x:x.get("updated_at",""),reverse=True):
            selected=self.status_filter.get()
            if selected=="Active" and c.get("status") not in ("Started","Waiting","Ready"): continue
            if selected not in ("Active","All") and c.get("status")!=selected: continue
            hay=" ".join(str(c.get(k,"")) for k in ("profile_name","email","status","notes")).lower()
            if q and q not in hay: continue
            self.visible.append(c["case_id"]); marker="▌ " if c.get("case_id")==self.case_id else "  "; self.list.insert("end",f"{marker}{c.get('profile_name','?')}  [{c.get('status','?')}]")
    def rescan_cases(self,quiet=False):
        result=self.store.recover()
        if not quiet: self.status_filter.set("All")
        self.refresh_cases()
        self.info.set(f"Rescan complete: {result['indexed']} case(s) indexed")
        if quiet: return result
        extra=f" Imported or repaired {result['imported']} record(s) from legacy files." if result["imported"] else ""
        self.modal("Rescan complete",f"Found {result['indexed']} case file(s) under cases/.{extra}\n\nStorage folder:\n{self.store.root}","success")
        return result
    def delete_case(self):
        sel=self.list.curselection(); cid=None; label="this case"
        if sel: cid=self.visible[sel[0]]
        elif self.case_id and self.store.get(self.case_id): cid=self.case_id
        if not cid: return self.modal("Delete case","Select a saved case to delete.","error")
        meta=next((x for x in self.store.all() if x.get("case_id")==cid),None)
        if meta and meta.get("profile_name"): label=f'"{meta.get("profile_name")}" [{meta.get("status","?")}]'
        def do_delete():
            if not self.store.delete(cid): return self.modal("Delete case","The case file could not be deleted.","error")
            if self.case_id==cid: self.new_case()
            self.refresh_cases(); self.info.set(f"Deleted case {label}"); self.modal("Deleted",f"Deleted {label}.","success")
        self.confirm_action(self.root,"Delete case",f"Permanently delete {label}? This cannot be undone.","Delete",do_delete,accent="#c92a3d")
    def open_selected(self,event=None):
        sel=self.list.curselection()
        if not sel:return
        c=self.store.get(self.visible[sel[0]])
        if not c: return self.modal("Open case","That case file is missing. Try Rescan / Recover.","error")
        self.loading=True; self.case_id=c["case_id"]; self.created_at=c.get("created_at")
        self.profile_name.set(c.get("profile_name","")); self.status.set(c.get("status","Started")); self.mode.set(REV_MODE.get(c.get("mode"),"Basic")); self.email.set(c.get("email","")); self.profile.set(c.get("profile_url","")); self.speed.set(c.get("speedtest_url",""))
        self.notes.delete("1.0","end"); self.notes.insert("1.0",c.get("notes","")); self.message.delete("1.0","end"); self.message.insert("1.0",c.get("message","") or "")
        self.loading=False
        if not self.message.get("1.0","end-1c").strip(): self.rebuild()
        self.info.set(f"Opened case: {c.get('profile_name','')}"); self.set_main_baseline(saved=True); self.update_summary(); self.refresh_cases()
        if self.case_id in self.visible:
            idx=self.visible.index(self.case_id); self.list.selection_set(idx); self.list.activate(idx); self.list.see(idx)
    def send(self):
        mode=MODE_LABELS.get(self.mode.get(),"basic"); email=clean(self.email.get(),160); profile=clean(self.profile.get(),350); speed=clean(self.speed.get(),350); msg=clean(self.message.get("1.0","end-1c"),430)
        if not self.profile_name.get().strip():
            name=self.profile_name_from_url(profile)
            if name:self.profile_name.set(name)
        if not self.profile_name.get().strip(): return self.modal("Validation error","Profile name is required before sending.","error")
        if not valid_email(email): return self.modal("Validation error","Enter a valid email address.","error")
        if not valid_url(profile): return self.modal("Validation error","Profile URL must start with http:// or https://.","error")
        if mode!="basic" and not valid_url(speed): return self.modal("Validation error","Speed Test URL must start with http:// or https://.","error")
        if not msg or any(x in msg for x in ("[EMAIL]","[PROFILE URL]","[SPEED TEST URL]")): return self.modal("Validation error","Complete all required fields and remove any placeholders.","error")
        previous_status=self.status.get() or "Started"
        self.status.set("Submitted")
        if not self.save_case(quiet=True):
            self.status.set(previous_status)
            return
        saved=self.store.get(self.case_id)
        if not saved or saved.get("status")!="Submitted":
            self.status.set(previous_status)
            return self.modal("Save error","The case could not be saved before sending to mIRC.","error")
        out=Path(self.args.outdir).resolve(); out.mkdir(parents=True,exist_ok=True); final=out/f"response_{self.args.request_id}.txt"
        fd,tmp=tempfile.mkstemp(prefix="handoff_",suffix=".tmp",dir=out,text=True)
        try:
            with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as h:h.write(msg)
            os.replace(tmp,final)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        self.info.set("Saved as Submitted and handed to mIRC.")
        self.modal("Sent to mIRC","Case saved as Submitted and the message was handed to mIRC.","success")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--request-id",required=True); p.add_argument("--mode",choices=TEMPLATES,default="basic"); p.add_argument("--outdir",required=True); p.add_argument("--cases")
    a=p.parse_args();
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",a.request_id): raise SystemExit("Invalid request id")
    if not a.cases: a.cases=str(Path(a.outdir).resolve().parent/"cases.json")
    root=tk.Tk(); app=App(root,a); app.mode.set(REV_MODE.get(a.mode,"Basic")); root.mainloop()
if __name__=="__main__": main()
