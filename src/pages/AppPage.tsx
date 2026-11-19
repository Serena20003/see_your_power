import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  InlineLoading,
  Select,
  SelectItem,
  TextInput,
  Theme,
  Tile,
} from "@carbon/react";
import { Chat, Close, Flash, Information, Search } from "@carbon/icons-react";
import { Link, useNavigate } from "react-router-dom";
import ForecastPanel from "../ForecastPanel";
import { useAuth } from "../lib/supabase/AuthContext";
import { useLocations } from "../features/locations/useLocations";
import { geocodeAddress } from "../lib/mapbox/geocode";
import { fetchHouseholdState, initializeHouseholdPlan, sendHouseholdChat, saveActionCompletion } from "../api";
import type { ApiPlanItem, HouseholdState } from "../api";
import type { HouseholdAction, Location } from "../types/database";
// ─── When-label formatting ────────────────────────────────────────────────────

function whenLabel(when: ApiPlanItem["when"]): string {
  switch (when) {
    case "two_days_before": return "2 DAYS BEFORE";
    case "night_before":    return "NIGHT BEFORE";
    case "one_hour_before": return "1 HOUR BEFORE";
    case "during_outage":   return "DURING OUTAGE";
  }
}

// ─── Assistant chat helpers ───────────────────────────────────────────────────

interface ChatMessage {
  role: "assistant" | "user";
  text: string;
}

const SUGGESTIONS = [
  "What should I prioritize?",
  "I added an oxygen concentrator",
  "I removed my EV",
  "How long should I prepare for?",
];

// ─── Main component ───────────────────────────────────────────────────────────

export default function AppPage() {
  const { session, profile: userProfile } = useAuth();
  const navigate = useNavigate();
  const redirectedRef = useRef(false);

  // ── Redirect guards ────────────────────────────────────────────────────────
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.includes("error=")) {
      window.history.replaceState(null, "", window.location.pathname);
      window.location.replace("/login?error=1");
      return;
    }
    if (!session || redirectedRef.current) return;
    redirectedRef.current = true;
    if (!userProfile?.first_name?.trim()) {
      navigate("/onboarding");
    }
  }, [session, userProfile, navigate]);

  // ── Initials ──────────────────────────────────────────────────────────────
  const initials =
    session && userProfile?.first_name && userProfile?.last_name
      ? `${userProfile.first_name[0]}${userProfile.last_name[0]}`.toUpperCase()
      : null;

  // ── Locations ─────────────────────────────────────────────────────────────
  const { locations, defaultLocation, loading: locLoading, refetch: refetchLocations } = useLocations();
  const [activeLocation, setActiveLocation] = useState<Location | null>(null);
  const [completionSaving, setCompletionSaving] = useState(false);
  const [chatSending, setChatSending] = useState(false);
  const activeLocationIdRef = useRef<string | null>(null);
  activeLocationIdRef.current = activeLocation?.id ?? null;

  // Load default location on mount (once locations are fetched)
  useEffect(() => {
    if (activeLocation === null && defaultLocation !== null) {
      setActiveLocation(defaultLocation);
    }
  }, [defaultLocation, activeLocation]);

  // ── Map / FIPS state ──────────────────────────────────────────────────────
  const [fips, setFips] = useState("06037");
  const [addressInput, setAddressInput] = useState("");
  const [flyToCoords, setFlyToCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);
  const [geocoding, setGeocoding] = useState(false);

  // When active location changes, fly the map to its coordinates
  useEffect(() => {
    if (activeLocation?.lat != null && activeLocation?.lon != null) {
      setFlyToCoords({ lat: activeLocation.lat, lon: activeLocation.lon });
      if (activeLocation.fips_code) {
        setFips(activeLocation.fips_code);
      }
    }
  }, [activeLocation]);

  async function handleAddressSearch(e: FormEvent) {
    e.preventDefault();
    const query = addressInput.trim();
    if (!query) return;
    setGeocodeError(null);
    setGeocoding(true);
    try {
      const result = await geocodeAddress(query);
      setFlyToCoords({ lat: result.lat, lon: result.lon });
    } catch (err) {
      setGeocodeError(err instanceof Error ? err.message : "Address not found.");
    } finally {
      setGeocoding(false);
    }
  }

  // ── Locations dropdown (authenticated) ────────────────────────────────────
  const locationDropdown =
    session !== null && locations.length > 0 ? (
      <div className="map-location-select">
        <Select
          id="location-select"
          labelText="Your saved locations"
          hideLabel
          size="sm"
          value={activeLocation?.id ?? ""}
          disabled={chatSending || completionSaving}
          onChange={(e) => {
            const loc = locations.find((l) => l.id === e.target.value) ?? null;
            if (loc) {
              setActiveLocation(loc);
              setPlanItems([]);
            }
          }}
        >
          {locations.map((loc) => (
            <SelectItem key={loc.id} value={loc.id} text={loc.name} />
          ))}
        </Select>
      </div>
    ) : null;

  const mapOverlay = (
    <>
      {locationDropdown}
      <form
        className="map-search-overlay"
        onSubmit={handleAddressSearch}
        role="search"
        aria-label="Search address"
      >
        <TextInput
          id="map-address-input"
          labelText="Search address or zip code"
          hideLabel
          placeholder="Search address or zip code…"
          value={addressInput}
          onChange={(e) => setAddressInput(e.target.value)}
          disabled={geocoding}
          invalid={!!geocodeError}
          invalidText={geocodeError ?? undefined}
          size="sm"
        />
        <Button
          type="submit"
          kind="primary"
          size="sm"
          renderIcon={Search}
          iconDescription="Search"
          hasIconOnly
          disabled={geocoding || !addressInput.trim()}
        />
      </form>
    </>
  );

  // ── Persisted plan and completion state ────────────────────────────────────
  const [planItems, setPlanItems] = useState<HouseholdAction[]>([]);
  const [planRevision, setPlanRevision] = useState(0);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const loadGeneration = useRef(0);

  function acceptHouseholdState(saved: HouseholdState) {
    if (activeLocationIdRef.current !== saved.location_id) return;
    setPlanItems(saved.plan_items);
    setPlanRevision(saved.revision);
    setActiveLocation((loc) => loc?.id === saved.location_id ? {
      ...loc, electronic_items: saved.electronic_items,
      plan_items: saved.plan_items, assistant_revision: saved.revision,
    } : loc);
  }

  useEffect(() => {
    const id = activeLocation?.id;
    if (!session || !id) return;
    const generation = ++loadGeneration.current;
    setPlanItems([]);
    setPlanLoading(true);
    setPlanError(null);
    void (async () => {
      try {
        let saved = await fetchHouseholdState(id);
        if (!saved.initialized) saved = await initializeHouseholdPlan(id);
        if (generation === loadGeneration.current) acceptHouseholdState(saved);
      } catch (error) {
        if (generation === loadGeneration.current) setPlanError(error instanceof Error ? error.message : "Could not load your plan.");
      } finally {
        if (generation === loadGeneration.current) setPlanLoading(false);
      }
    })();
    return () => { ++loadGeneration.current; };
    // Changes to saved devices must not regenerate and overwrite an edited checklist.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLocation?.id, session?.user.id]);

  async function toggleCompletion(id: string, completed: boolean) {
    if (!activeLocation || completionSaving || chatSending) return;
    setCompletionSaving(true);
    setPlanError(null);
    try {
      acceptHouseholdState(await saveActionCompletion(activeLocation.id, id, completed, planRevision));
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "Could not save this step.");
    } finally {
      setCompletionSaving(false);
    }
  }

  const checkedCount = planItems.filter((item) => item.completed).length;
  const totalCount = planItems.length;

  // ── Assistant FAB + drawer ─────────────────────────────────────────────────
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const chatFeedRef = useRef<HTMLDivElement>(null);

  // Initialize chat with greeting when drawer opens (or location changes)
  useEffect(() => {
    if (session && userProfile?.first_name) {
      const greeting = `Hi ${userProfile.first_name}. Tell me about your household — I can update your saved electronic devices and outage action items. What would you like to change or ask about?`;
      setChatMessages([{ role: "assistant", text: greeting }]);
    }
  }, [session?.user.id, activeLocation?.id]);

  // Scroll to bottom of chat feed
  useEffect(() => {
    if (drawerOpen && chatFeedRef.current) {
      chatFeedRef.current.scrollTop = chatFeedRef.current.scrollHeight;
    }
  }, [chatMessages, drawerOpen]);

  async function sendChatMessage(text: string) {
    if (!text.trim() || chatSending || completionSaving || planLoading) return;
    if (!activeLocation) {
      setChatMessages((prev) => [...prev, { role: "assistant", text: "Select a saved location so I can use the right devices and action items." }]);
      return;
    }
    const locationId = activeLocation.id;
    const history = chatMessages.map((m) => ({ role: m.role, text: m.text }));
    setChatMessages((prev) => [...prev, { role: "user", text: text.trim() }]);
    setChatInput("");
    setChatSending(true);
    try {
      const response = await sendHouseholdChat(locationId, text.trim(), history);
      if (activeLocationIdRef.current !== locationId) return;
      acceptHouseholdState(response);
      setChatMessages((prev) => [...prev, { role: "assistant", text: response.reply }]);
      if (response.saved) {
        setPlanError(null);
        await refetchLocations();
      }
    } catch (error) {
      if (activeLocationIdRef.current === locationId) {
        setChatMessages((prev) => [...prev, { role: "assistant", text: error instanceof Error ? error.message : "I couldn't confirm a save. Please check your plan before trying again." }]);
      }
    } finally {
      setChatSending(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <Theme theme="g10">
      <div className="site-shell">
        {/* ── Header ── */}
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark"><Flash size={20} /></span>
            <span>runway<span className="brand-period">.</span></span>
          </div>
          <div className="header-right">
            <a className="header-model-link" href="/model-sources">Model sources ↗</a>
            {initials ? (
              <Link
                to="/profile"
                className="avatar"
                aria-label="Go to profile"
                style={{ textDecoration: "none" }}
              >
                {initials}
              </Link>
            ) : (
              <Button kind="ghost" size="sm" onClick={() => { window.location.href = "/login"; }}>
                Log in / Sign up
              </Button>
            )}
          </div>
        </header>

        <main>
          {/* ── Greeting ── */}
          <div className="welcome">
            {session && userProfile?.first_name && (
              <p>
                Hello, {userProfile.first_name}. Here is what the next few days may mean for your power.
              </p>
            )}
          </div>

          {/* ── Forecast + plan layout ──
              Logged-in:  map (left) | plan checklist (right), forecast detail below
              Logged-out: map (left) | forecast detail (right)
          ── */}
          <div id="forecast">
            <ForecastPanel
              fips={fips}
              onFipsChange={setFips}
              flyToCoords={flyToCoords}
              mapOverlay={mapOverlay}
              rightSlot={session !== null ? (
                <div className="plan-side-panel" id="my-plan">
                  <div className="plan-side-header">
                    <span className="section-kicker">YOUR OUTAGE PLAN</span>
                    <h2 id="plan-title">
                      {activeLocation
                        ? `Plan for ${activeLocation.name}`
                        : "Your outage preparation plan"}
                    </h2>
                  </div>

                  <div className="plan-side-body">
                    {/* Loading state */}
                    {planLoading && (
                      <div style={{ padding: "16px 0" }}>
                        <InlineLoading description="Generating your plan…" />
                      </div>
                    )}

                    {/* No location selected yet */}
                    {!planLoading && !activeLocation && !locLoading && (
                      <p className="panel-description">
                        {locations.length === 0
                          ? "Add a location in your profile to generate a personalized outage plan."
                          : "Select a saved location above to see your plan."}
                      </p>
                    )}

                    {/* No devices */}
                    {!planLoading && activeLocation && (!activeLocation.electronic_items?.length) && (
                      <p className="panel-description">
                        No devices saved for <strong>{activeLocation.name}</strong>. Ask the assistant to add electronic devices, or edit them in{" "}
                        <Link to="/profile">your profile</Link>.
                      </p>
                    )}

                    {/* Plan error */}
                    {planError && !planLoading && (
                      <p className="panel-description" style={{ color: "#da1e28" }}>{planError}</p>
                    )}

                    {!planLoading && !planError && activeLocation && planItems.length === 0 && (
                      <p className="panel-description">Your action list is empty. Ask the assistant to create a plan or add a step.</p>
                    )}

                    {/* Plan items checklist */}
                    {!planLoading && planItems.length > 0 && (
                      <>
                        {/* Progress bar */}
                        <div className="plan-progress" aria-label={`${checkedCount} of ${totalCount} steps completed`}>
                          <span style={{ width: totalCount > 0 ? `${(checkedCount / totalCount) * 100}%` : "0%" }} />
                        </div>
                        <p className="panel-meta" style={{ color: "#6f6f6f", fontSize: 11, marginBottom: 8 }}>
                          {checkedCount} / {totalCount} steps completed
                        </p>

                        <div className="plan-items">
                          {planItems.map((item, idx) => {
                            const key = item.id;
                            const checked = item.completed;
                            return (
                              <div key={key} className={`plan-item${checked ? " is-done" : ""}`}>
                                <Checkbox
                                  id={`plan-item-${idx}`}
                                  labelText={
                                    <span className="plan-copy">
                                      <span className="plan-meta">{whenLabel(item.when)}</span>
                                      <strong>{item.title_en ?? item.title}</strong>
                                      {item.reason_en || item.reason ? (
                                        <span className="plan-why">
                                          <span>WHY</span>
                                          {item.reason_en ?? item.reason}
                                        </span>
                                      ) : null}
                                    </span>
                                  }
                                  checked={checked}
                                  disabled={completionSaving || chatSending}
                                  onChange={(_: unknown, { checked: c }: { checked: boolean }) => {
                                    void toggleCompletion(item.id, c);
                                  }}
                                />
                              </div>
                            );
                          })}
                        </div>

                        <div className="plan-note">
                          <Information size={16} />
                          <span>
                            Plan generated from your saved devices. Use the assistant (bottom-right) to update your device list.
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              ) : undefined}
            />
          </div>
        </main>

        <footer className="site-footer">
          <span>Runway · Experimental model preview</span>
          <span>
            County impact estimates come from a fitted experimental model, with weather shown separately.
            No numeric outage probabilities are presented as validated predictions.
          </span>
        </footer>
      </div>

      {/* ── Assistant FAB (authenticated) ── */}
      {session !== null && !drawerOpen && (
        <button
          className="assistant-fab"
          aria-label="Open plan assistant"
          title="Plan assistant"
          onClick={() => setDrawerOpen(true)}
        >
          <Chat size={22} />
        </button>
      )}

      {/* ── Assistant drawer (authenticated) ── */}
      {session !== null && drawerOpen && (
        <div className="assistant-drawer" role="complementary" aria-label="Plan assistant">
          {/* Header */}
          <div className="assistant-drawer-header">
            <span>PLAN ASSISTANT · WATSONX</span>
            <button
              className="assistant-drawer-close"
              aria-label="Close assistant"
              onClick={() => setDrawerOpen(false)}
            >
              <Close size={18} />
            </button>
          </div>

          {/* Chat feed */}
          <div className="assistant-drawer-body" ref={chatFeedRef}>
            {chatMessages.map((msg, idx) => (
              <div key={idx} className={`chat-bubble${msg.role === "user" ? " user" : ""}`}>
                <span>{msg.role === "user" ? "YOU" : "ASSISTANT"}</span>
                <p>{msg.text}</p>
              </div>
            ))}
            {chatSending && (
              <div className="chat-bubble">
                <span>ASSISTANT</span>
                <InlineLoading description="Thinking…" />
              </div>
            )}
          </div>

          {/* Suggestion chips */}
          {!chatSending && chatMessages.length <= 1 && (
            <div className="chat-suggestions" style={{ padding: "8px 12px 0" }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void sendChatMessage(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input + Send */}
          <div className="assistant-drawer-footer">
            <form
              className="chat-form"
              onSubmit={(e) => { e.preventDefault(); void sendChatMessage(chatInput); }}
            >
              <input
                type="text"
                placeholder="Ask about your plan…"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                disabled={chatSending || completionSaving || planLoading}
                aria-label="Message to assistant"
              />
              <Button
                type="submit"
                kind="primary"
                size="sm"
                disabled={chatSending || completionSaving || planLoading || !chatInput.trim()}
              >
                Send
              </Button>
            </form>
          </div>
        </div>
      )}
    </Theme>
  );
}
