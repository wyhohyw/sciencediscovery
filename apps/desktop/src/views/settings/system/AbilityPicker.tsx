// Copyright (C) 2026 Huawei Technologies Co., Ltd
// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import type { ConnectorManifest, SkillDescriptor } from "../../../api/types.js";
import { TEXT } from "../../../text.js";
import { SettingsModal } from "../SettingsModal.js";
import { SpecialistMark, SpecialistSearch, SpecialistSourceTabs, type SpecialistSource } from "./SpecialistParts.js";

export type AbilityTab = "skills" | "connectors";
interface Props {
  connectors: ConnectorManifest[];
  initialTab: AbilityTab;
  onApply: (selection: { connectorIds: string[]; skillIds: string[] }) => void;
  onClose: () => void;
  selectedConnectorIds: string[];
  selectedSkillIds: string[];
  skills: SkillDescriptor[];
}

/** Selection belongs to this drawer until Confirm; Cancel never edits the form. */
export function AbilityPicker({ connectors, initialTab, onApply, onClose, selectedConnectorIds, selectedSkillIds, skills }: Props) {
  const [skillIds, setSkillIds] = useState([...selectedSkillIds]);
  const [connectorIds, setConnectorIds] = useState([...selectedConnectorIds]);
  const [source, setSource] = useState<SpecialistSource>("all");
  const [query, setQuery] = useState("");
  const labels = TEXT.settings.system.specialists;
  const showingSkills = initialTab === "skills";
  const ids = showingSkills ? skillIds : connectorIds;
  const rows = showingSkills ? skills.map(s => ({ id: s.id, name: s.name, description: s.description, official: s.source === "built-in" }))
    : connectors.map(c => ({ id: c.id, name: c.displayName ?? c.id, description: c.commercialUseConstraints || c.id, official: c.trustLevel === "bundled" }));
  const needle = query.trim().toLocaleLowerCase();
  const filtered = rows.filter(row => (source === "all" || row.official === (source === "official"))
    && `${row.name} ${row.description}`.toLocaleLowerCase().includes(needle));
  const choose = (id: string, checked: boolean) => {
    const next = checked ? [...new Set([...ids, id])] : ids.filter(value => value !== id);
    if (showingSkills) setSkillIds(next); else setConnectorIds(next);
  };
  return <SettingsModal side label={showingSkills ? labels.configureSkills : labels.configureMcp} onClose={onClose}>
    <div className="specialist-drawer-body">
      <SpecialistSourceTabs value={source} onChange={setSource} />
      <SpecialistSearch value={query} onChange={setQuery} />
      <div className="specialist-picker-grid">
        {filtered.map(row => <label className={`specialist-capability-card${ids.includes(row.id) ? " is-selected" : ""}`} key={row.id}>
          <div className="specialist-capability-top"><SpecialistMark name={row.name} /><div><strong>{row.name}</strong><span className="specialist-badge">{row.official ? labels.builtIn : labels.external}</span></div>
            <input aria-label={row.name} type="checkbox" checked={ids.includes(row.id)} onChange={event => choose(row.id, event.target.checked)} />
          </div><p>{row.description}</p>
        </label>)}
      </div>
      {filtered.length === 0 ? <p className="specialist-empty">{labels.noResults}</p> : null}
    </div>
    <footer className="specialist-drawer-footer"><span>{labels.selectedCount(ids.length)}</span><div>
      <button className="pill-button" type="button" onClick={onClose}>{TEXT.settings.cancel}</button>
      <button className="pill-button is-dark" type="button" onClick={() => onApply({ connectorIds, skillIds })}>{labels.confirm}</button>
    </div></footer>
  </SettingsModal>;
}
