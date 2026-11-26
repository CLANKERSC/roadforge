import type { Dispatch } from "react";
import { CORRIDOR, hintFor, type Draft, type DraftAction, type Game } from "../lib/selection";
import type { Plan, SourceDesc } from "../lib/types";

interface Props {
  draft: Draft;
  dispatch: Dispatch<DraftAction>;
  name: string;
  onName: (name: string) => void;
  games: Game[];
  onGames: (games: Game[]) => void;
  plan: Plan | null;
  planning: boolean;
  routeWarning: string | null;
  canBuild: boolean;
  onBuild: () => void;
}

const LAYER_LABEL = { elevation: "Terrain height", imagery: "Photo from above", vectors: "Roads and buildings" } as const;

export function formatResolution(src: SourceDesc): string {
  if (src.resolution_m === null) return "map data";
  return src.resolution_m < 1 ? `${Math.round(src.resolution_m * 100)} cm` : `${src.resolution_m} m`;
}

export function qualityOf(src: SourceDesc): "high" | "low" {
  return src.resolution_m === null || src.resolution_m <= 5 ? "high" : "low";
}

export function BuildPanel(p: Props) {
  const { draft, dispatch, plan } = p;
  const toggleGame = (g: Game) =>
    p.onGames(p.games.includes(g) ? p.games.filter((x) => x !== g) : [...p.games, g]);

  return (
    <section className="build">
      <Step n={1} title="Draw it">
        <div className="seg" role="group" aria-label="What to draw">
          <button className={draft.mode === "area" ? "on" : ""} onClick={() => dispatch({ type: "setMode", mode: "area" })}>
            Area
          </button>
          <button className={draft.mode === "route" ? "on" : ""} onClick={() => dispatch({ type: "setMode", mode: "route" })}>
            Road A to B
          </button>
        </div>
        {draft.mode === "area" && (
          <div className="seg small-seg" role="group" aria-label="Area shape">
            <button className={draft.shape === "box" ? "on" : ""} onClick={() => dispatch({ type: "setShape", shape: "box" })}>
              Box
            </button>
            <button
              className={draft.shape === "polygon" ? "on" : ""}
              onClick={() => dispatch({ type: "setShape", shape: "polygon" })}
            >
              Free shape
            </button>
          </div>
        )}
        <p className="hint" data-testid="hint">
          {hintFor(draft)}
        </p>
        {draft.mode === "route" && (
          <label className="slider">
            <span>
              Width each side: <strong>{draft.corridorM} m</strong>
            </span>
            <input
              type="range"
              min={CORRIDOR.min}
              max={CORRIDOR.max}
              step={CORRIDOR.step}
              value={draft.corridorM}
              onChange={(e) => dispatch({ type: "setCorridor", metres: Number(e.target.value) })}
            />
          </label>
        )}
        <div className="row">
          <button onClick={() => dispatch({ type: "undo" })} disabled={draft.points.length === 0} title="Ctrl+Z">
            Undo
          </button>
          {draft.mode === "route" && (
            <button onClick={() => dispatch({ type: "reverse" })} disabled={draft.points.length < 2}>
              Reverse
            </button>
          )}
          <button onClick={() => dispatch({ type: "clear" })} disabled={draft.points.length === 0} title="Esc">
            Clear
          </button>
        </div>
        {p.routeWarning && <p className="warn small">{p.routeWarning}</p>}
      </Step>

      <Step n={2} title="Name it">
        <input
          className="text"
          value={p.name}
          onChange={(e) => p.onName(e.target.value)}
          placeholder="e.g. Brno to Tišnov"
          maxLength={60}
          aria-label="Map name"
        />
        {plan?.ids && p.name.trim() && (
          <p className="muted small">
            Folder name in the game: <code>{plan.ids.ac}</code>
          </p>
        )}
      </Step>

      <Step n={3} title="Pick your game">
        <label className="check">
          <input type="checkbox" checked={p.games.includes("ac")} onChange={() => toggleGame("ac")} /> Assetto Corsa
        </label>
        <label className="check">
          <input type="checkbox" checked={p.games.includes("beamng")} onChange={() => toggleGame("beamng")} /> BeamNG.drive
        </label>
        {p.games.length === 0 && <p className="warn small">Pick at least one game.</p>}
      </Step>

      <Step n={4} title="Build it">
        {!plan && <p className="muted small">{p.planning ? "Checking your selection…" : "Draw something on the map first."}</p>}
        {plan && plan.problems.map((msg, i) => (
          <p key={i} className="error small" role="alert">
            {msg}
          </p>
        ))}
        {plan && plan.area_km2 !== undefined && (
          <dl className="readout" data-testid="readout">
            {plan.route_km != null && (
              <>
                <dt>Road length</dt>
                <dd>{plan.route_km.toFixed(1)} km</dd>
              </>
            )}
            <dt>Area</dt>
            <dd>
              {plan.area_km2.toFixed(2)} km² ({plan.size_km?.[0].toFixed(1)} × {plan.size_km?.[1].toFixed(1)} km)
            </dd>
            <dt>Download</dt>
            <dd>about {plan.est_download_mb} MB</dd>
            <dt>Time</dt>
            <dd>about {plan.est_minutes} min</dd>
          </dl>
        )}
        {plan && Object.keys(plan.layers).length > 0 && (
          <ul className="quality" data-testid="quality">
            {(Object.keys(LAYER_LABEL) as (keyof typeof LAYER_LABEL)[]).map((layer) => {
              const src = plan.layers[layer]?.chosen;
              if (!src) return null;
              return (
                <li key={layer} className={`q-${qualityOf(src)}`}>
                  <div className="q-top">
                    <span>{LAYER_LABEL[layer]}</span>
                    <strong>{formatResolution(src)}</strong>
                  </div>
                  <div className="muted small">
                    {src.name} · <span className="licence">{src.licence}</span>
                  </div>
                  {src.note && <div className="warn small">{src.note}</div>}
                </li>
              );
            })}
          </ul>
        )}
        <button className="primary big" disabled={!p.canBuild} onClick={p.onBuild}>
          Build it
        </button>
        <p className="muted small">
          The build downloads the map data, builds the roads and the ground, checks that every road is drivable and
          writes a BeamNG.drive map. Assetto Corsa comes later.
        </p>
      </Step>
    </section>
  );
}

function Step(props: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="step">
      <h3>
        <span className="step-n">{props.n}</span> {props.title}
      </h3>
      {props.children}
    </div>
  );
}
