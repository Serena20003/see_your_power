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
import { updateLocation } from "../features/locations/locationService";
import { geocodeAddress } from "../lib/mapbox/geocode";
import { fetchPlan, fetchPlan as fetchPlanAPI } from "../api";
import type { ApiPlanItem, PlanRequestPayload } from "../api";
import type { ElectronicItem, Location } from "../types/database";

// ─── Device-name → API DeviceType mapping ────────────────────────────────────

function itemsToDevices(items: ElectronicItem[]): string[] {
  const devices: string[] = [];
  for (const item of items) {
    const n = item.name.toLowerCase();
    if (n.includes("oxygen")) devices.push("oxygen_concentrator");
    else if (n.includes("insulin")) devices.push("insulin_fridge");
    else if (n.includes("cpap") || n.includes("bipap")) devices.push("cpap");
    else if (n.includes("dialysis")) devices.push("home_dialysis");
    else if (n.includes("wheelchair")) devices.push("power_wheelchair");
    // EV / electric vehicle → hasEV flag only, not a device string
  }
  return devices;
}

function itemsHasEV(items: ElectronicItem[]): boolean {
  return items.some((i) => {
    const n = i.name.toLowerCase();
    return n.includes("ev") || n.includes("electric vehicle");
  });
}

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
          onChange={(e) => {
            const loc = locations.find((l) => l.id === e.target.value) ?? null;
            if (loc) {
              setActiveLocation(loc);
              setPlanItems([]);
              setCheckedItems({});
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

  // ── Plan panel state ───────────────────────────────────────────────────────
  const [planItems, setPlanItems] = useState<ApiPlanItem[]>([]);
  const [checkedItems, setCheckedItems] = useState<Record<string, boolean>>({});
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  // Track which location id the plan was last generated for
  const planLocationRef = useRef<string | null>(null);

  // Fetch plan whenever activeLocation changes (or its electronic_items change)
  const activeForecastRef = useRef<{ probability: number; primaryCause?: string } | null>(null);

  async function loadPlan(loc: Location) {
    if (!loc.electronic_items?.length) {
      setPlanItems([]);
      return;
    }
    setPlanLoading(true);
    setPlanError(null);
    try {
      const devices = itemsToDevices(loc.electronic_items);
      const hasEV = itemsHasEV(loc.electronic_items);
      const payload: PlanRequestPayload = {
        message: "Generate my outage preparation plan.",
        resident: {
          name: userProfile?.first_name ?? "Resident",
          devices,
          hasEV,
          livesAlone: false,
          language: "en",
        },
        forecast: {
          probability: activeForecastRef.current?.probability ?? 0.5,
          primaryCause: activeForecastRef.current?.primaryCause ?? "wind",
        },
      };
      const resp = await fetchPlanAPI(payload);
      setPlanItems(resp.items ?? []);
      // Reset checked state when items are refreshed
      setCheckedItems({});
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Could not load plan.");
    } finally {
      setPlanLoading(false);
    }
  }

  useEffect(() => {
    if (!session || !activeLocation) return;
    // Only regenerate if location changed
    if (planLocationRef.current === activeLocation.id && planItems.length > 0) return;
    planLocationRef.current = activeLocation.id;
    setCheckedItems({});
    void loadPlan(activeLocation);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLocation, session]);

  const checkedCount = Object.values(checkedItems).filter(Boolean).length;
  const totalCount = planItems.length;

  // ── Assistant FAB + drawer ─────────────────────────────────────────────────
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const chatFeedRef = useRef<HTMLDivElement>(null);

  // Initialize chat with greeting when drawer opens (or location changes)
  useEffect(() => {
    if (session && userProfile?.first_name) {
      const greeting = `Hi ${userProfile.first_name}. Tell me about your household — I can help you update your device list and refine your outage plan.`;
      setChatMessages([{ role: "assistant", text: greeting }]);
    }
  }, [session, userProfile?.first_name]);

  // Scroll to bottom of chat feed
  useEffect(() => {
    if (drawerOpen && chatFeedRef.current) {
      chatFeedRef.current.scrollTop = chatFeedRef.current.scrollHeight;
    }
  }, [chatMessages, drawerOpen]);

  async function sendChatMessage(text: string) {
    if (!text.trim() || chatSending) return;
    const userMsg: ChatMessage = { role: "user", text: text.trim() };
    const history = chatMessages.map((m) => ({ role: m.role, text: m.text }));
    setChatMessages((prev) => [...prev, userMsg]);
    setChatInput("");
    setChatSending(true);

    try {
      const devices = activeLocation ? itemsToDevices(activeLocation.electronic_items ?? []) : [];
      const hasEV = activeLocation ? itemsHasEV(activeLocation.electronic_items ?? []) : false;
      const payload: PlanRequestPayload = {
        message: text.trim(),
        history,
        resident: {
          name: userProfile?.first_name ?? "Resident",
          devices,
          hasEV,
          livesAlone: false,
          language: "en",
        },
        forecast: {
          probability: activeForecastRef.current?.probability ?? 0.5,
          primaryCause: activeForecastRef.current?.primaryCause ?? "wind",
        },
      };
      const resp = await fetchPlan(payload);
      const assistantMsg: ChatMessage = { role: "assistant", text: resp.reply };
      setChatMessages((prev) => [...prev, assistantMsg]);

      // ── Assistant-driven device mutation detection ────────────────────────
      if (activeLocation && resp.items && resp.items.length > 0) {
        // If the assistant returned updated items, regenerate the plan
        setPlanItems(resp.items);
        setCheckedItems({});
      }

      // Keyword detection: "added" / "removed" in reply → try to mutate items
      const replyLower = resp.reply.toLowerCase();
      const deviceMutationWords = ["added", "removed", "deleted", "updated your device"];
      const hasMutation = deviceMutationWords.some((w) => replyLower.includes(w));
      if (hasMutation && activeLocation) {
        // Best-effort: refetch to pick up any server-side changes
        await refetchLocations();
        // Find the refreshed location
        // (we can't easily re-read state here; a followup effect will catch it)
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Something went wrong.";
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", text: `Sorry, I couldn't process that. ${errMsg}` },
      ]);
    } finally {
      setChatSending(false);
    }
  }

  // When refetch runs and locations update, sync activeLocation's electronic_items
  useEffect(() => {
    if (!activeLocation) return;
    const refreshed = locations.find((l) => l.id === activeLocation.id);
    if (refreshed && JSON.stringify(refreshed.electronic_items) !== JSON.stringify(activeLocation.electronic_items)) {
      setActiveLocation(refreshed);
    }
  }, [locations, activeLocation]);

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

          {/* ── Forecast section ── */}
          <div id="forecast">
            <ForecastPanel
              fips={fips}
              onFipsChange={setFips}
              flyToCoords={flyToCoords}
              mapOverlay={mapOverlay}
            />
          </div>

          {/* ── Plan panel (authenticated) ── */}
          {session !== null && (
            <section className="plan-section" aria-labelledby="plan-title" id="my-plan">
              <div className="section-bridge">
                <div>
                  <span className="section-kicker">YOUR OUTAGE PLAN</span>
                  <h2 id="plan-title">
                    {activeLocation
                      ? `Plan for ${activeLocation.name}`
                      : "Your outage preparation plan"}
                  </h2>
                </div>
              </div>

              <Tile className="plan-card">
                {/* Loading state */}
                {planLoading && (
                  <div style={{ padding: "24px 0" }}>
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
                    No devices saved for <strong>{activeLocation.name}</strong>. Visit{" "}
                    <Link to="/profile">your profile</Link> to add medical or power-dependent devices.
                  </p>
                )}

                {/* Plan error */}
                {planError && !planLoading && (
                  <p className="panel-description" style={{ color: "#da1e28" }}>{planError}</p>
                )}

                {/* Plan items checklist */}
                {!planLoading && planItems.length > 0 && (
                  <>
                    <p className="panel-description">
                      Personalized for {activeLocation?.name ?? "your location"} based on your saved devices.
                    </p>

                    {/* Progress bar */}
                    <div className="plan-progress" aria-label={`${checkedCount} of ${totalCount} steps completed`}>
                      <span style={{ width: totalCount > 0 ? `${(checkedCount / totalCount) * 100}%` : "0%" }} />
                    </div>
                    <p className="panel-meta" style={{ color: "#6f6f6f", fontSize: 11, marginBottom: 8 }}>
                      {checkedCount} / {totalCount} steps completed
                    </p>

                    <div className="plan-items">
                      {planItems.map((item, idx) => {
                        const key = `${item.when}-${idx}`;
                        const checked = checkedItems[key] ?? false;
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
                              onChange={(_: unknown, { checked: c }: { checked: boolean }) => {
                                setCheckedItems((prev) => ({ ...prev, [key]: c }));
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
              </Tile>
            </section>
          )}
        </main>

        <footer className="site-footer">
          <span>Runway · Static product preview</span>
          <span>
            County forecast data is sample output from an unvalidated model stub.
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
                disabled={chatSending}
                aria-label="Message to assistant"
              />
              <Button
                type="submit"
                kind="primary"
                size="sm"
                disabled={chatSending || !chatInput.trim()}
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
