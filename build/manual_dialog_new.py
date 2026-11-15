class ManualBetDialog(tk.Toplevel):
    """手工补录：点号码 / 点生肖 / 点波段，再填一个金额，回车成注。

    不用打字。号码是多选「每号各记这个金额」，生肖是「一共这个金额、摊到它名下
    的号上」——这两种口径在实体单上本来就不一样，界面上写清楚，清单里也写出来。
    波段（波色 / 头组 / 大小单双 / 尾数）这一版只灰着摆出来，看得见有哪些玩法、
    不能点：它们各自一套赔率，记进号码盘会把 10 块记成 170 块。
    """

    HINT = "点号码（每号各记）／点生肖（总额摊开）／填金额回车加入"

    COLORS = {"红波": "#c0392b", "蓝波": "#1f6feb", "绿波": "#1a7f37"}
    PICK_BORDER = "#f59e0b"

    OPTIONS = [
        ("红波", "波色"), ("蓝波", "波色"), ("绿波", "波色"),
        ("红单", "波色单双"), ("红双", "波色单双"),
        ("蓝单", "波色单双"), ("蓝双", "波色单双"),
        ("绿单", "波色单双"), ("绿双", "波色单双"),
        ("0头", "头组"), ("1头", "头组"), ("2头", "头组"),
        ("3头", "头组"), ("4头", "头组"),
        ("大", "大小"), ("小", "大小"), ("单", "单双"), ("双", "单双"),
        ("尾数", "尾数"), ("五行", "五行"),
    ]

    def __init__(self, master, mapping, grid, chats, on_submit=None, demo=False):
        super().__init__(master)
        self.title("手工记账")
        self.configure(bg=CLR_CARD)
        self.transient(master)
        self.mapping = mapping or {}
        self.grid = list(grid or [])
        self.chats = list(chats or [])
        self.on_submit = on_submit
        self.notes = []              # 已加入的注：{applied, total, desc}
        self.sel_nums = set()
        self.sel_zodiac = None
        self._num_lb = {}
        self._z_lb = {}
        self._event = None
        self._build()
        self.update_idletasks()
        self.geometry("680x820+%d+%d" % (
            max(0, master.winfo_rootx() + (master.winfo_width() - 680) // 2),
            max(0, master.winfo_rooty() + (master.winfo_height() - 820) // 2)))
        self.update()
        self.minsize(680, 820)
        self.maxsize(680, 820)
        self._repaint()
        try:
            self.grab_set()
        except Exception:
            pass
        if demo:
            self.after(250, self._demo)

    def _demo(self):
        """只在 MSB_MANUAL=2 调试时用：预填几注，方便截图看效果。"""
        self.v_chat.set(self.chats[0] if self.chats else "老郭")
        self.sel_nums = {"06", "18"}
        self.v_amt.set("50")
        self._repaint()
        self._add()
        self.sel_zodiac = "牛"
        self.v_amt.set("100")
        self._repaint()
        self._add()
        self.sel_zodiac = None
        self.sel_nums = {"06", "18"}
        self.v_amt.set("50")
        self._repaint()
        self.lb_note.configure(text="")
        self._refresh_foot()

    # ---------- 界面 ----------
    def _build(self):
        wrap = tk.Frame(self, bg=CLR_CARD)
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        wrap.grid_columnconfigure(0, weight=1)

        tk.Label(wrap, text="手工记账", font=FONT_T, bg=CLR_CARD, fg="#111827"
                 ).grid(row=0, column=0, sticky="w")
        tk.Label(wrap, text=self.HINT, font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).grid(row=1, column=0, sticky="w", pady=(2, 8))

        # 号码盘：一行一个生肖，格子里是号，底色就是波色
        board = tk.Frame(wrap, bg=CLR_CARD)
        board.grid(row=2, column=0, sticky="w")
        for r, rowd in enumerate(self.grid):
            tk.Label(board, text=rowd["zodiac"], font=FONT_S, bg=CLR_CARD,
                     fg="#374151", width=2, anchor="w").grid(row=r, column=0, sticky="w")
            for c, cell in enumerate(rowd["cells"], 1):
                n = cell["num"]
                color = self.COLORS.get(cell.get("color"), "#6b7280")
                lb = tk.Label(board, text=n, font=FONT_NUM, bg=color, fg="white",
                              width=3, cursor="hand2", padx=1, pady=2,
                              highlightthickness=2, highlightbackground=color)
                lb.grid(row=r, column=c, padx=1, pady=1)
                lb.bind("<Button-1>", lambda e, x=n: self._pick_num(x))
                self._num_lb[n] = lb

        # 生肖：点一下等于选了它名下所有号，但按「总额摊开」算
        zbar = tk.Frame(wrap, bg=CLR_CARD)
        zbar.grid(row=3, column=0, sticky="w", pady=(10, 0))
        tk.Label(zbar, text="生肖", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).grid(row=0, column=0, padx=(0, 6))
        for i, rowd in enumerate(self.grid, 1):
            z = rowd["zodiac"]
            b = tk.Label(zbar, text=z, font=FONT_S, bg="#eef0f3", fg="#374151",
                         width=2, cursor="hand2", padx=2, pady=3)
            b.grid(row=0, column=i, padx=1)
            b.bind("<Button-1>", lambda e, x=z: self._pick_zodiac(x))
            self._z_lb[z] = b

        # 波段：这一版灰着，只让人看见有哪些玩法，点不动
        obar = tk.Frame(wrap, bg=CLR_CARD)
        obar.grid(row=4, column=0, sticky="w", pady=(8, 0))
        tk.Label(obar, text="波段", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).grid(row=0, column=0, rowspan=2, padx=(0, 6))
        for i, (name, kind) in enumerate(self.OPTIONS):
            r, c = divmod(i, 10)
            b = tk.Label(obar, text=name, font=FONT_S, bg="#f7f8fa", fg="#c4cad3",
                         width=4, cursor="arrow", padx=2, pady=3)
            b.grid(row=r, column=c + 1, padx=1, pady=1)
        tk.Label(obar, text="（本期未开放）", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).grid(row=0, column=11, rowspan=2, padx=(8, 0))

        # 已选 + 金额 + 加入
        pick = tk.Frame(wrap, bg=CLR_CARD)
        pick.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        tk.Label(pick, text="已选", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).pack(side="left")
        self.lb_pick = tk.Label(pick, text="（还没选）", font=FONT_S,
                                bg=CLR_CARD, fg=CLR_MUTED, anchor="w")
        self.lb_pick.pack(side="left", padx=(8, 16))
        tk.Label(pick, text="金额", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).pack(side="left")
        self.v_amt = tk.StringVar()
        self.en_amt = tk.Entry(pick, textvariable=self.v_amt, font=FONT, width=9,
                               relief="flat", bg="#fbfcfd", fg="#111827",
                               highlightbackground=CLR_LINE, highlightthickness=1,
                               insertbackground="#111827", justify="right")
        self.en_amt.pack(side="left", padx=(8, 8), ipady=3)
        self.en_amt.bind("<Return>", lambda e: self._add())
        self.btn_add = tk.Button(pick, text="加入", font=FONT, bg=CLR_BLUE2, fg="white",
                                 relief="flat", bd=0, width=6, cursor="hand2",
                                 command=self._add)
        self.btn_add.pack(side="left")

        tk.Label(wrap, text="本次要记的（点「加入」累加）", font=FONT_S,
                 bg=CLR_CARD, fg=CLR_MUTED).grid(row=6, column=0, sticky="w", pady=(12, 4))
        lbox = tk.Frame(wrap, bg=CLR_CARD, highlightbackground=CLR_LINE,
                        highlightthickness=1)
        lbox.grid(row=7, column=0, sticky="nsew")
        wrap.grid_rowconfigure(7, weight=1)
        self.lst = tk.Listbox(lbox, height=6, font=FONT_S, bd=0, relief="flat",
                              bg="#fbfcfd", fg="#111827", activestyle="none",
                              highlightthickness=0, selectbackground="#dbeafe")
        self.lst.pack(fill="both", expand=True, padx=2, pady=2)

        foot = tk.Frame(wrap, bg=CLR_CARD)
        foot.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        self.lb_total = tk.Label(foot, text="合计 0 元", font=FONT_B,
                                 bg=CLR_CARD, fg=CLR_RED)
        self.lb_total.pack(side="left")
        tk.Label(foot, text="挂在谁名下", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).pack(side="left", padx=(18, 6))
        self.v_chat = tk.StringVar()
        self.cb_chat = ttk.Combobox(foot, textvariable=self.v_chat, font=FONT_S,
                                    width=18, values=self.chats)
        self.cb_chat.pack(side="left")
        self.cb_chat.bind("<KeyRelease>", self._on_typed)
        self.cb_chat.bind("<<ComboboxSelected>>", self._on_typed)

        tk.Button(foot, text="取消", font=FONT, bg="#eef0f3", fg="#374151", relief="flat",
                  bd=0, width=7, cursor="hand2", command=self.destroy).pack(side="right")
        self.btn_ok = tk.Button(foot, text="记账", font=FONT, bg=CLR_BLUE, fg="white",
                                relief="flat", bd=0, width=8, cursor="hand2",
                                command=self._submit)
        self.btn_ok.pack(side="right", padx=(0, 6))
        tk.Button(foot, text="删掉选中", font=FONT_S, bg="#eef0f3", fg="#374151",
                  relief="flat", bd=0, width=8, cursor="hand2",
                  command=self._drop).pack(side="right", padx=(0, 12))

        self.lb_note = tk.Label(wrap, text="", font=FONT_S, bg=CLR_CARD, fg=CLR_BAD,
                                anchor="w")
        self.lb_note.grid(row=9, column=0, sticky="w", pady=(6, 0))

        self.bind("<Escape>", lambda e: self.destroy())

    # ---------- 点选 ----------
    def _pick_num(self, n):
        self.sel_zodiac = None
        if n in self.sel_nums:
            self.sel_nums.discard(n)
        else:
            self.sel_nums.add(n)
        self._repaint()

    def _pick_zodiac(self, z):
        self.sel_nums = set()
        self.sel_zodiac = None if self.sel_zodiac == z else z
        self._repaint()

    def _repaint(self):
        sel = self.sel_nums
        for n, lb in self._num_lb.items():
            on = n in sel
            lb.configure(text=("✓" + n) if on else n,
                         width=4 if on else 3,
                         font=FONT_B if on else FONT_NUM,
                         highlightbackground=self.PICK_BORDER if on else lb.cget("bg"))
        for z, b in self._z_lb.items():
            on = (self.sel_zodiac == z)
            b.configure(bg="#fde68a" if on else "#eef0f3",
                        fg="#92400e" if on else "#374151")

        if self.sel_zodiac:
            nums = sorted(self.mapping.get(self.sel_zodiac, []))
            self.lb_pick.configure(
                text="%s（%s）· 填的金额是这一组的总数" % (self.sel_zodiac, "/".join(nums)),
                fg="#92400e")
        elif self.sel_nums:
            self.lb_pick.configure(
                text="%s · 每号各记填的金额" % " ".join(sorted(self.sel_nums)),
                fg="#92400e")
        else:
            self.lb_pick.configure(text="（还没选）", fg=CLR_MUTED)

        self._refresh_foot()

    # ---------- 加入 ----------
    def _add(self):
        raw = (self.v_amt.get() or "").strip().replace("，", "")
        try:
            amount = float(raw)
        except Exception:
            return self._say("金额写个数字，比如 50")
        if amount <= 0:
            return self._say("金额要大于 0")

        if self.sel_zodiac:
            nums = sorted(self.mapping.get(self.sel_zodiac, []))
            if not nums:
                return self._say("生肖表里没有「%s」" % self.sel_zodiac)
            applied = self._split(nums, amount, pool=True)
            desc = "%s（%s）各 %s，合计 %s" % (
                self.sel_zodiac, "/".join(nums),
                self._money(round(amount / len(nums), 2)), self._money(amount))
        elif self.sel_nums:
            nums = sorted(self.sel_nums)
            applied = self._split(nums, amount, pool=False)
            desc = "%s 各 %s，合计 %s" % (
                " ".join(nums), self._money(amount),
                self._money(round(amount * len(nums), 2)))
        else:
            return self._say("先点号码或生肖")

        self.notes.append({"applied": applied, "desc": desc,
                           "total": round(sum(applied.values()), 2)})
        self.sel_nums = set()
        self.sel_zodiac = None
        self.v_amt.set("")
        self._say("")
        self._refresh_list()
        self._repaint()
        self.en_amt.focus_set()

    @staticmethod
    def _split(nums, amount, pool):
        out = {}
        if pool:
            n = len(nums)
            per = round(amount / n, 2)
            last = round(amount - per * (n - 1), 2)
            for i, num in enumerate(nums):
                out[num] = last if i == n - 1 else per
        else:
            for num in nums:
                out[num] = round(amount, 2)
        return out

    @staticmethod
    def _money(v):
        s = ("%.2f" % float(v)).rstrip("0").rstrip(".")
        return s if s else "0"

    def _drop(self):
        idx = self.lst.curselection()
        if not idx:
            return self._say("先在清单里点一行")
        self.notes.pop(idx[0])
        self._refresh_list()

    def _refresh_list(self):
        self.lst.delete(0, "end")
        for i, n in enumerate(self.notes):
            self.lst.insert("end", "%d. %s" % (i + 1, n["desc"]))
        self.lb_total.configure(
            text="合计 %s 元 / %d 注" % (
                self._money(sum(n["total"] for n in self.notes)), len(self.notes)))
        self._refresh_foot()

    def _refresh_foot(self):
        chat = (self.v_chat.get() or "").strip()
        ok = bool(self.notes) and bool(chat)
        try:
            self.btn_ok.configure(state="normal" if ok else "disabled")
        except Exception:
            pass
        if not ok and self.notes and not chat:
            self.lb_note.configure(text="填一下挂在谁名下")

    def _say(self, msg):
        self.lb_note.configure(text=msg)
        if self._event:
            try:
                self.after_cancel(self._event)
            except Exception:
                pass
        if msg:
            self._event = self.after(3000, lambda: self.lb_note.configure(text=""))

    def _on_typed(self, event=None):
        self.lb_note.configure(text="")
        self._refresh_foot()

    def _submit(self):
        if not self.notes:
            return
        chat = (self.v_chat.get() or "").strip()
        if not chat:
            self._say("填一下挂在谁名下")
            self.cb_chat.focus_set()
            return
        if self.on_submit:
            self.on_submit(self.notes, chat)
        self.destroy()
