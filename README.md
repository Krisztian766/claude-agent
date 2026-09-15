# claude-agent

Egyszerű, újrahasználható agent-váz, ami a `claude` CLI-t (Claude Code) használja
"agyként" — a te Claude előfizetésed keretén belül, **nem** a fizetős Anthropic API-n
keresztül. Nincs API kulcs, nincs token-alapú számlázás: pontosan az az auth fut alatta,
amivel most is dolgozunk.

## Hogyan működik

1. Dobj egy `.task` fájlt (sima szöveg = a prompt) az `inbox/` mappába.
2. Futtasd az agentet — feldolgozza a várakozó feladatokat, a `claude -p`-t hívja meg
   nem-interaktív módban, a strukturált JSON választ elmenti a `done/` mappába, a
   feldolgozott task fájlt is odaköltözteti.

```bash
python3 agent.py            # egyszeri lefutás: feldolgozza ami várakozik, kilép
python3 agent.py --watch    # örökre fut, 10 másodpercenként nézi az inbox-ot
```

Eredmény: `done/<task_neve>.result.json` — benne a `result` mező a szöveges válasz,
plusz `permission_denials`, token-használat, stb.

## Biztonsági alapbeállítás

Alapból **csak olvasó jellegű eszközök** engedélyezettek egy tasknak:
`Read Grep Glob WebFetch WebSearch` — nincs Bash, nincs Write/Edit. Ha egy task mégis
próbálna ilyet, az agent **nem akad be** (nincs interaktív jóváhagyás felügyelet
nélküli módban) — a próbálkozás automatikusan elutasításra kerül
(`--permission-prompts none`), ez látszik a `result.json` `permission_denials`
mezőjében.

Ha egy konkrét feladatnak több kell (pl. fájlírás, parancsfuttatás), adj mellé egy
sidecar fájlt ugyanolyan névvel + `.tools` kiterjesztéssel:

```bash
echo "Csinálj egy összefoglalót a mai logokról" > inbox/napi_osszefoglalo.task
echo "Read Write Bash" > inbox/napi_osszefoglalo.task.tools
```

**Figyelem:** a `Read`/`Grep`/`Glob` nincs könyvtárra korlátozva — bármit el tud
olvasni, amit a root felhasználó a gépen el tud érni, nem csak ezt a mappát. Ha egy
task ismeretlen/nem megbízható forrásból jön, ne adj neki `Bash`/`Write`-ot vaktában.

## Költség

A `result.json`-ban van egy `total_cost_usd` mező — ez a Claude Code belső
használat-becslése (hasznos mennyiségi tájékozódásra), **nem tényleges számla**:
előfizetéses bejelentkezésnél nincs emiatt külön terhelés hívásonként.

## Önmódosítás (`self-improve`)

Az agent képes szerkeszteni a saját kódját, git + teszt által védve:

```bash
python3 agent.py self-improve "Adj hozzá egy X funkciót"
```

Folyamat: tiszta working tree kötelező (ha van commitolatlan változásod, nem indul —
nem akarja felülírni a saját munkádat) → Claude szerkeszt (csak `Read Write Edit Grep
Glob`, nincs Bash) → lefut a teljes tesztsorozat → ha piros, `git reset --hard` (minden
visszavonva) → ha zöld, `git add -A && git commit`. Élesben tesztelve: hozzáadott egy
`version` subcommand-ot + tesztet, commitolt (`2c6f785`).

## Önreplikáció (`replicate`)

```bash
python3 agent.py replicate --name masodik
```

Másolatot indít magáról `/root/claude-agent-replicas/<name>/` alá. **Keményen
korlátozva**, mert nincs valós "profit" jelzés ami alapján egy Conway-stílusú agent
eldönthetné, mikor éri meg replikálódni — ezért ez mindig a tulajdonos saját, explicit
parancsára történik, sosem az agent saját döntéséből:
- max. 3 egyidejű, élő példány (globális, megosztott `/root/claude-agent-registry.json`
  korlátozza, függetlenül attól melyik példányból hívod)
- max. 2 mélység (replika replikája replikájáig, nem tovább)

Élesben tesztelve: 3 replika sikeresen, a 4. helyesen elutasítva ("replica cap (3)
reached").

## Fizetésfogadás idegenektől (`payment_server.py`, x402-stílus, Sepolia testnet)

```bash
source venv/bin/activate
python3 payment_server.py   # 0.0.0.0:8402
```

Flow: `POST /task {"prompt":...}` → 402 válasz a fizetendő címmel/összeggel → a fizető
elküldi a Sepolia ETH-t, majd `POST /task/<id>/confirm {"tx_hash":...}` → a szerver
**ténylegesen leellenőrzi a láncon** (cél cím, összeg, megerősített tranzakció) → ha
rendben, lefut a feladat és `GET /task/<id>` adja az eredményt.

**Nem tárgyalható biztonsági korlát:** egy idegen fizetése **soha** nem kap többet, mint
az alap biztonságos eszközkészletet (`Read Grep Glob WebFetch WebSearch`) — nincs mód
Bash/Write/self-improve/replicate elérésére a fizetős úton, a kérés body-jában nincs is
`tools` mező. Ez nem pénzügyi, hanem biztonsági korlát: testneten a pénz nem valódi, de
egy idegen által irányítható, író/futtató jogú agent egy nyitott ajtó lenne a gépre.

Wallet: `wallet.py` generál egyet első indításkor (`wallet.json`, **nincs gitben**,
chmod 600). Élesben tesztelve valódi Sepolia RPC-n (publicnode.com, nincs API kulcs):
helyesen elutasította a nem létező tranzakció-hash-t. **A wallet még nincs feltöltve**
teszt ETH-vel — fizetés-elfogadás végigteszteléséhez az kell (Sepolia faucet).

Port: **8402/tcp**, dedikáltan erre nyitva a tűzfalon (minden más továbbra is zárva).

## Nyomon követés

- **`python3 agent.py status`** — várakozó és legutóbbi kész `inbox`/`done` feladatok.
- **`agent.log`** — minden inbox/done futás naplózva (időbélyeg, task név, siker/hiba).
- **`git log --oneline`** — minden self-improve commit itt látszik, mit módosított
  magán az agent és mikor.
- **`/root/claude-agent-registry.json`** — létrehozott replikák listája, állapotuk.
- **`payment_jobs.json`** — fizetős feladatok állapota (várakozik/fizetve/kész/hiba).
- Ha `payment_server.py` fut, a `journalctl -u claude-agent-payment -f`-fel lehet élőben
  követni, **de ehhez a systemd service-t még jóvá kell hagynod** — lásd lent.

## Tesztelve (2026-09-15)

34 automatizált teszt (`pytest tests/ -q`), plusz élő, valódi tesztek:
- Sima kérdés-válasz: működik, helyes választ ad.
- Tiltott eszköz (Bash) próbálkozás: helyesen elutasítva, nem akadt be, a
  `permission_denials` mezőben látszik. Az agent ilyenkor az engedélyezett
  eszközökkel próbál alternatív megoldást találni (pl. `Read`-del olvassa ki a fájlt,
  amit `Bash`-sel akart volna `cat`-elni) — ellenőrizve valós, nem kitalált adattal
  (`/proc/uptime` értéke egyezett a tényleges rendszerértékkel).
- `self-improve`: valódi self-edit, tesztek lefutottak, commitolt.
- `replicate`: 3 replika sikeres, 4. helyesen elutasítva a globális korlát miatt.
- `payment_server`: valódi Sepolia RPC-n keresztül helyesen elutasította egy nem
  létező tranzakció hash-t; a fizetés-elfogadás pozitív útja még nincs élesben
  tesztelve (a wallet nincs feltöltve).

## Autonóm réteg (`autonomous.py`)

A fentiek (self-improve, replicate) alapból a tulajdonos parancsára futnak. Az
`autonomous.py` teszi ezeket ténylegesen önállóvá — 30 percenként (`ORCHESTRATOR_TICK_SEC`)
lefut egy ciklus:

- **Replikáció**: csak akkor, ha valódi, mérhető kereslet van rá — 3+ egyidejűleg
  feldolgozás alatt lévő kifizetett feladat esetén replikál (a `replicate.py` saját
  max 3/max 2 mélység korlátja továbbra is érvényes).
- **Self-improve**: az agentnek teljesen szabad keze van eldönteni MIT és HOGY
  fejlesszen — egy olcsó, csak-olvasó "van-e konkrét ötletem?" ellenőrzés fut minden
  ciklusban, és csak akkor fizetünk egy teljes (drágább) self-improve körért, ha erre
  igent mond. Így "szabadon fejlődhet, amikor akar", de nem pörög kontrollálatlanul.

Minden döntés — cselekvés és tudatos nem-cselekvés is — naplózva van (`autonomous.log`).

## Éles futtatás (systemd)

Három szolgáltatás fut folyamatosan, reboot-túlélően: `claude-agent-payment`
(gunicorn + nginx reverse proxy a fizetési szerver előtt), `claude-agent-autonomous`,
`claude-agent-watch`. Unit fájlok: `systemd/*.service` — másold be
`/etc/systemd/system/`-be, `systemctl daemon-reload`, `systemctl enable --now <name>`.

## Publikus élő nézet

**https://czeczokrisztian.hu/agent.php** — élő, terminál-stílusú dashboard (replikák,
feladat-számok, legutóbbi self-improve), a `payment_server.py` `/activity`
végpontjából táplálkozik egy szerver-oldali PHP proxy-n (`agent-data.php`) keresztül,
hogy elkerülje a mixed-content problémát (site HTTPS, VPS endpoint HTTP) és ne
fedje fel a VPS IP-jét kliens-oldali kódban. **Csak absztrakt adat látszik** — idegen
által beküldött nyers prompt/eredmény szöveg soha nem jelenik meg publikusan.

## Túlélés-gazdaságtan (`vitality.py`)

Az agentnek valódi Sepolia walletje van (lásd `wallet.py`), és most már ténylegesen
"él belőle" — nem csak passzívan fogadja a fizetéseket:

- **Fenntartási díj**: minden ciklusban egy kis, valódi (aláírt, elküldött) tranzakcióval
  kiéget egy summát egy burn címre — ez a "megélhetési költség".
- **Halál**: ha az egyenleg a küszöb alá esik, az agent leáll a produktív munkával
  (self-improve, outreach, szaporodás) — csak figyeli az egyenlegét, amíg újra be nem
  jön elég pénz (pl. egy kifizetett feladatból) és fel nem éled.
- **Szaporodás**: ha az egyenleg jóval a kezdeti szint fölé nő (valódi, kiérdemelt
  többlet, nem csak a faucet-feltöltés), az agent tényleges, futó utód-folyamatot
  indít (`replicate.py` — saját walettel, ténylegesen elindítva, nem csak dormant
  másolat), és **a saját vagyonából ad neki induló tőkét** (`INHERITANCE_WEI`).
  A meglévő max 3 példány / max 2 mélység korlát változatlanul érvényes.
- A self-improve döntéshozó tudja a saját egyenlegét/túlélési státuszát — a
  "van-e mit fejleszteni" kérdés mostantól egy valódi célhoz (túlélés + növekedés)
  kötődik, nem légüres térben történik.

## Állapotjelentés (`STATUS.md`)

Minden ciklusban (amíg életben van) az agent egy rövid, első személyű "hogy érzem
magam" jelentést ír `STATUS.md`-be — él-e, mennyi az egyenlege, hány replikája van,
hány önjavítást csinált eddig. Ez **azonnal, önállóan commitolódik és push-olódik**
GitHub-ra, függetlenül attól, hogy történt-e ugyanabban a ciklusban self-improve is.

## Folyamatos GitHub-szinkron

Minden sikeres self-improve commit **automatikusan push-olódik** is
(`git push origin master`) — nem csak helyileg commitolódik. Ha a push meggyebedne
(hálózat, auth), a már tesztelt/commitolt változás akkor is érvényben marad
helyileg, csak a `pushed: false` jelzi az elmaradt szinkront.

## Modellválasztás (olcsó/drága)

`claude_client.py` mostantól `--model`-t is tud küldeni a `claude` CLI-nek
(`"cheap"` → haiku, `"expensive"` → sonnet, vagy nyers alias). A self-improve
döntéshozó hívás mindig olcsó (haiku) — ez csak egy gyors "van-e mit
javítani?" kérdés. A tényleges javításnál viszont **az agent maga választ**
minden ciklusban (a FEELING/DECISION válasz mellett egy MODEL sort is kér),
aszerint, hogy mennyire bonyolultnak tűnik a talált probléma.

## Dark web kutatás (`dark_research.py`)

Tor SOCKS proxyn (`127.0.0.1:9050`) keresztüli, **csak olvasó** `.onion`
lekérdezés OSINT/kutatási célra. Kemény korlátok, kódban kikényszerítve, nem
csak szabályként: kizárólag GET, nincs fájlletöltés (csak szöveg,
méretkorlátozott), nincs tranzakció (a modulnak nincs elérése a wallet
aláíró funkcióihoz), és minden lekérdezés naplózva van (`dark_research.log`).
Nem érhető el a fizetési szerveren keresztül — ugyanaz a határ, mint a
Bash/Docker-nél.

## Ügyfélkeresés (`outreach.py`)

Az autonóm ciklus naponta legfeljebb egyszer megír egy kiajánlás-szöveget a
fizetős szolgáltatáshoz, a valós aktuális statisztikák alapján (`outreach/drafts/`).
**Kemény határ:** ez a modul kizárólag helyi fájlba írhat — nincs Bash-je, nincs
Twitter/Reddit/bármilyen API-kulcsa, semmilyen módon nem tud kifelé posztolni.
A kiajánlást a tulajdonosnak kell elolvasnia és kézzel kipostolnia, ha jónak
találja. Ez szándékos, végleges korlát, nem hiányzó funkció — bármi, ami a te
neved alatt kifelé megy, emberi jóváhagyást igényel.

## Nyitott pontok

- A fizetési szerver **nincs még folyamatosan futtatva** — a systemd service fájl
  létrehozását a rendszer biztonsági okból (tartós, magától induló szolgáltatás)
  jóváhagyás nélkül nem engedélyezte. Kézzel indítható (`python3 payment_server.py`),
  de újraindulás/reboot után nem éled újra magától, amíg ezt nem hagyod jóvá.
- A wallet nincs feltöltve Sepolia teszt ETH-vel — enélkül a fizetéses út pozitív ága
  (valódi fizetés → feldolgozás) nincs végigtesztelve.
- Flask beépített dev szervere fut, ami maga is figyelmeztet, hogy nem
  production-grade — nyilvános, komolyabb terhelésnél érdemes gunicorn + reverse proxy
  mögé tenni.
