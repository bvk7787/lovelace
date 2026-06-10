"""Lovelace CLI — ZOIA Euroburo patch generator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lovelace.encoder import decode, encode
from lovelace.patch import PatchConfig, build_patch

app = typer.Typer(
    name="lovelace",
    help="Generate .zoia patches for the Euroburo paraphonic relationship sequencer.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def generate(
    output: Annotated[Path, typer.Option("-o", "--output", help="Output .zoia file")] = Path("lovelace.zoia"),
    name: Annotated[str, typer.Option("--name", help="Patch name (max 16 chars)")] = "lovelace",
    bpm: Annotated[float, typer.Option("--bpm", help="Clock tempo in BPM")] = 120.0,
    key: Annotated[int, typer.Option("--key", help="Scale root note (0=C, 1=C#, ..., 11=B)")] = 0,
    scale: Annotated[int, typer.Option(
        "--scale",
        help="Scale mode index (0=major, 1=dorian, 2=phrygian, 3=lydian, "
             "4=mixolydian, 5=natural minor, 6=locrian, 7=chromatic)",
    )] = 1,
    phase: Annotated[int, typer.Option("--phase", help="Build phase (1=V1 only, 2=+V2)")] = 2,
    visuals: Annotated[bool, typer.Option("--visuals/--no-visuals", help="Include pixel step display")] = False,
    dump_json: Annotated[bool, typer.Option("--json", help="Also write patch JSON alongside the binary")] = False,
):
    """Generate the paraphonic relationship sequencer patch."""
    cfg = PatchConfig(
        name=name,
        bpm=bpm,
        key=key,
        scale=scale,
        phase=phase,
        visuals=visuals,
    )

    console.print(f"[bold]Building patch[/bold] — phase={phase}, bpm={bpm}, key={key}, scale={scale}")
    patch = build_patch(cfg)

    n_modules = patch["meta"]["n_modules"]
    n_connections = patch["meta"]["n_connections"]
    cpu = patch["meta"]["cpu"]
    n_pages = patch["meta"]["n_pages"]

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[dim]modules[/dim]", str(n_modules))
    table.add_row("[dim]connections[/dim]", str(n_connections))
    table.add_row("[dim]pages[/dim]", str(n_pages))
    table.add_row("[dim]cpu estimate[/dim]", f"{cpu:.1f}%")
    console.print(table)

    encode(patch, output_path=output)
    console.print(f"[green]✓[/green] Wrote {output}")

    if dump_json:
        json_path = output.with_suffix(".json")
        json_path.write_text(json.dumps(patch, indent=2))
        console.print(f"[green]✓[/green] Wrote {json_path}")


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(help="Path to a .zoia binary file")],
    json_out: Annotated[bool, typer.Option("--json", help="Print raw JSON")] = False,
):
    """Inspect a .zoia binary and display its contents."""
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        raise typer.Exit(1)

    data = path.read_bytes()
    patch = decode(data)

    if json_out:
        console.print_json(json.dumps(patch, indent=2))
        return

    console.print(f"\n[bold]{patch['name']}[/bold]  ({path.name})")
    console.print(f"  modules: {patch['meta']['n_modules']}  "
                  f"connections: {patch['meta']['n_connections']}  "
                  f"pages: {patch['meta']['n_pages']}  "
                  f"cpu: {patch['meta']['cpu']:.1f}%\n")

    t = Table("idx", "type", "name", "page", "pos", "params", "cpu")
    for m in patch["modules"]:
        pos = m["position"]
        pos_str = str(pos[0]) if isinstance(pos, list) else str(pos)
        t.add_row(
            str(m["number"]),
            m["type"],
            m.get("name", ""),
            str(m["page"]),
            pos_str,
            str(m["params"]),
            f"{m['cpu']:.2f}",
        )
    console.print(t)

    if patch["connections"]:
        console.print(f"\n[bold]Connections[/bold] ({len(patch['connections'])})")
        ct = Table("src_mod", "src_block", "dst_mod", "dst_block", "strength")
        for c in patch["connections"]:
            ct.add_row(
                str(c["source_raw"]),
                str(c["source_block_raw"]),
                str(c["dest_raw"]),
                str(c["dest_block_raw"]),
                str(c["strength_raw"]),
            )
        console.print(ct)


@app.command()
def roundtrip(
    path: Annotated[Path, typer.Argument(help="Path to a .zoia binary to validate")],
):
    """Validate encode→decode→encode round-trip fidelity for a .zoia file."""
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        raise typer.Exit(1)

    original = path.read_bytes()
    patch = decode(original)
    re_encoded = encode(patch)

    orig_active = original[:patch["size"] * 4]
    re_active = re_encoded[:patch["size"] * 4]

    if orig_active == re_active:
        console.print("[green]✓ Round-trip identical[/green]")
    else:
        mismatches = [(i, original[i], re_encoded[i])
                      for i in range(min(len(orig_active), len(re_active)))
                      if original[i] != re_encoded[i]]
        console.print(f"[red]✗ {len(mismatches)} byte(s) differ[/red]")
        for offset, orig_b, new_b in mismatches[:20]:
            console.print(f"  offset {offset:#06x}: orig={orig_b:#04x} re={new_b:#04x}")
        if len(mismatches) > 20:
            console.print(f"  ... and {len(mismatches) - 20} more")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
