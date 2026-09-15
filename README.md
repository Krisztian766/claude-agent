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

## Folyamatos (autonóm) üzemeltetés

Ha azt szeretnéd, hogy az agent önállóan figyeljen és dolgozzon (pl. systemd service
`--watch` móddal, ahogy a korábbi honeypot-riport is időzítve frissült), szólj — abból
a mintából egy `claude-agent.service` egység gyorsan összerakható, csak előbb kell egy
konkrét feladat, amire ráállítjuk.

## Tesztelve (2026-09-15)

- Sima kérdés-válasz: működik, helyes választ ad.
- Tiltott eszköz (Bash) próbálkozás: helyesen elutasítva, nem akadt be, a
  `permission_denials` mezőben látszik. Az agent ilyenkor az engedélyezett
  eszközökkel próbál alternatív megoldást találni (pl. `Read`-del olvassa ki a fájlt,
  amit `Bash`-sel akart volna `cat`-elni) — ellenőrizve valós, nem kitalált adattal
  (`/proc/uptime` értéke egyezett a tényleges rendszerértékkel).
