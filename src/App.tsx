import { useMemo, useState } from "react";
import { Button, Checkbox, InlineLoading, Tag, Theme, Tile } from "@carbon/react";
import { ArrowRight, Chat, Flash, Send, SettingsAdjust } from "@carbon/icons-react";
import ForecastPanel from "./ForecastPanel";
import { createPlan, initialProfile, profileChoices, type PlanItem, type Profile, type ProfileKey } from "./mockModel";
import { fetchPlan, type ApiPlanItem } from "./api";

type ChatMessage = { role: "assistant" | "user"; text: string };
type FactSource = "sample" | "interview" | "chat" | "edited";
type Sources = Record<ProfileKey, FactSource>;

const interviewSteps: { title: string; description: string; keys: ProfileKey[] }[] = [
  { title: "What needs steady power?", description: "Choose what matters for care and food at home.", keys: ["oxygen", "insulin", "food"] },
  { title: "What can move around?", description: "We can suggest times for flexible tasks and charging.", keys: ["ev", "laundry", "costAware"] },
  { title: "Who is in your circle?", description: "Add a gentle reminder to check in with family.", keys: ["family"] },
];

function InterviewPanel({ profile, onComplete }: { profile: Profile; onComplete: (profile: Profile | null) => void }) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Profile>(profile);
  const current = interviewSteps[step];
  return <Tile className="interview-card" role="region" aria-labelledby="interview-title">
    <div className="panel-topline"><span className="section-kicker">GETTING TO KNOW YOU</span><Tag type="blue" size="sm">{step + 1} of {interviewSteps.length}</Tag></div>
    <h2 id="interview-title">A plan that fits your life</h2><p className="interview-intro">A few starting questions help explain which actions appear below. You can change every answer later.</p>
    <div className="interview-progress" aria-hidden="true">{interviewSteps.map((_, index) => <span className={index <= step ? "active" : ""} key={index} />)}</div>
    <h3>{current.title}</h3><p className="interview-description">{current.description}</p>
    <div className="interview-choices">{current.keys.map((key) => { const choice = profileChoices.find((item) => item.key === key)!; return <div className="interview-choice" key={key}><Checkbox id={`interview-${key}`} checked={draft[key]} onChange={() => setDraft((value) => ({ ...value, [key]: !value[key] }))} labelText={<span><strong>{choice.title}</strong><small>{choice.detail}</small></span>} /></div>; })}</div>
    <div className="interview-actions">{step > 0 && <Button kind="tertiary" size="sm" type="button" onClick={() => setStep(step - 1)}>Back</Button>}<Button size="sm" type="button" onClick={() => step < interviewSteps.length - 1 ? setStep(step + 1) : onComplete(draft)}>{step < interviewSteps.length - 1 ? "Next" : "See my plan"}</Button></div>
    <button className="skip-interview" type="button" onClick={() => onComplete(null)}>Explore with sample answers</button>
  </Tile>;
}

function PlanAndSettingsPanel({ profile, sources, plan, checked, setChecked, onToggle, onRestartInterview }: { profile: Profile; sources: Sources; plan: PlanItem[]; checked: Record<string, boolean>; setChecked: (value: Record<string, boolean>) => void; onToggle: (key: ProfileKey) => void; onRestartInterview: () => void }) {
  const completed = plan.filter((item) => checked[item.id]).length;
  const sourceLabel: Record<FactSource, string> = { sample: "Sample answer", interview: "Starting questions", chat: "Added in chat", edited: "You corrected this" };
  return <Tile className="plan-card" role="region" aria-labelledby="plan-title">
    <div className="panel-topline"><span className="section-kicker">YOUR PLAN</span><Tag type="teal" size="sm">{completed} of {plan.length} done</Tag></div>
    <h2 id="plan-title">Small steps, chosen for you.</h2><p className="panel-description">The selected window's forecast and the answers below shape this plan. Each step shows why it is here.</p>
    <div className="plan-progress" role="progressbar" aria-label="Plan progress" aria-valuemin={0} aria-valuemax={plan.length} aria-valuenow={completed}><span style={{ width: `${completed / plan.length * 100}%` }} /></div>
    <div className="plan-list">{plan.map((item) => <div className={`plan-item ${checked[item.id] ? "is-done" : ""}`} key={item.id}><Checkbox id={`plan-${item.id}`} checked={!!checked[item.id]} onChange={() => setChecked({ ...checked, [item.id]: !checked[item.id] })} labelText={<span className="plan-copy"><span className="plan-meta">{item.when} <span aria-hidden="true">·</span> {item.category === "essential" ? "POWER & CARE" : item.category === "savings" ? "SAVINGS" : "HOME"}</span><strong>{item.title}</strong><span className="plan-why"><span>WHY THIS IS IN YOUR PLAN</span>{item.why}</span></span>} /></div>)}</div>
    <div className="plan-note"><Flash size={18} /><span>{profile.oxygen ? "Check the actual backup battery run time and device instructions before relying on it during an outage." : "This plan includes everyday preparation and can change as you update your answers."}</span></div>
    <section className="known-section" id="settings" aria-labelledby="known-title"><div className="known-heading"><div><span className="section-kicker">YOUR SETTINGS · WHAT RUNWAY KNOWS</span><h2 id="known-title">Your answers, in your hands.</h2><p className="panel-description">See what shaped your plan, where each answer came from, and correct anything that is wrong.</p></div><SettingsAdjust size={22} aria-hidden="true" /></div>
      <div className="profile-list">{profileChoices.map((choice) => <div className="profile-item" key={choice.key}><Checkbox id={`profile-${choice.key}`} checked={profile[choice.key]} onChange={() => onToggle(choice.key)} labelText={<span className="profile-copy"><strong>{choice.title}</strong><small>{choice.detail}</small></span>} /><span className="fact-source">{sourceLabel[sources[choice.key]]}</span></div>)}</div>
      <div className="known-footer"><p className="profile-note">Your answers stay on this page until you close it. Using the assistant sends your message and relevant answers to the plan service and, when available, watsonx.ai.</p><Button kind="tertiary" size="sm" type="button" onClick={onRestartInterview}>Retake starting questions</Button></div>
    </section>
  </Tile>;
}

/** Convert a watsonx `when` string to a display label matching mockModel style. */
function whenLabel(when: ApiPlanItem["when"]): string {
  switch (when) {
    case "two_days_before": return "TWO DAYS BEFORE";
    case "night_before":    return "NIGHT BEFORE";
    case "one_hour_before": return "ONE HOUR BEFORE";
    case "during_outage":   return "DURING OUTAGE";
  }
}

function AssistantPanel({
  profile,
  onSetProfile,
  onPlanItems,
}: {
  profile: Profile;
  onSetProfile: (key: ProfileKey) => void;
  onPlanItems: (items: ApiPlanItem[]) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([{
    role: "assistant",
    text: "Hi Rosa. Tell me about your household — a device, routine, or concern — and I'll add personalised steps to your plan.",
  }]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(message: string) {
    const trimmed = message.trim();
    if (!trimmed || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: trimmed }]);
    setLoading(true);

    // Mirror keyword hints into profile flags (still instant UX)
    const text = trimmed.toLowerCase();
    if (text.includes("insulin") || text.includes("medicine")) onSetProfile("insulin");
    else if (text.includes("ev") || text.includes("electric car")) onSetProfile("ev");
    else if (text.includes("laundry")) onSetProfile("laundry");
    else if (text.includes("bill") || text.includes("cheap") || text.includes("price")) onSetProfile("costAware");
    else if (text.includes("family") || text.includes("check in")) onSetProfile("family");

    try {
      const devices: string[] = [];
      if (profile.oxygen) devices.push("oxygen_concentrator");
      if (profile.insulin) devices.push("insulin_fridge");
      if (trimmed.toLowerCase().includes("insulin") || trimmed.toLowerCase().includes("medicine")) devices.push("insulin_fridge");
      if (trimmed.toLowerCase().includes("cpap")) devices.push("cpap");
      if (trimmed.toLowerCase().includes("dialysis")) devices.push("home_dialysis");
      if (trimmed.toLowerCase().includes("wheelchair")) devices.push("power_wheelchair");

      const result = await fetchPlan({
        message: trimmed,
        history: messages.slice(1).slice(-6),
        resident: {
          name: "Rosa Martinez",
          age: 78,
          devices: [...new Set(devices)],
          hasEV: profile.ev || trimmed.toLowerCase().includes("ev"),
          livesAlone: true,
          language: "en",
        },
        forecast: { probability: 0.74, primaryCause: "wind" },
      });

      if (result.items.length) onPlanItems(result.items);

      const fallbackNote = {
        missing_credentials: "Add watsonx credentials to the plan service.",
        project_unavailable: "IBM could not find the configured project. Check its project ID and region.",
        runtime_unavailable: "Connect a watsonx.ai Runtime service to this IBM project.",
        model_unavailable: "The configured watsonx model is unavailable. Check its model ID.",
        access_denied: "The IBM API key does not have access to this project.",
        watsonx_error: "The watsonx request failed. Check the plan service log.",
      } as const;
      const sourceNote = result.source === "fallback"
        ? `\n\nUsing template steps: ${result.fallback_reason ? fallbackNote[result.fallback_reason] : "watsonx is unavailable."}`
        : "";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: `${result.reply}${result.items.length ? "\n\nI added related steps to your plan below." : ""}${sourceNote}` },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", text: "I couldn't reach the plan service right now. Check that the backend is running at VITE_API_BASE_URL." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Tile className="assistant-card" role="region" aria-labelledby="assistant-title">
      <div className="assistant-top">
        <div className="assistant-icon"><Chat size={21} /></div>
        <div>
          <span className="section-kicker">PLAN ASSISTANT · WATSONX</span>
          <h2 id="assistant-title">Talk through your plan</h2>
        </div>
      </div>
      <p className="assistant-disclaimer">Powered by watsonx.ai. Uses template steps if the service is unavailable.</p>
      <div className="chat-feed" role="log" aria-label="Plan assistant conversation" aria-live="polite">
        {messages.map((message, index) => (
          <div className={`chat-bubble ${message.role}`} key={index}>
            <span>{message.role === "assistant" ? "Runway" : "You"}</span>
            <p style={{ whiteSpace: "pre-line" }}>{message.text}</p>
          </div>
        ))}
        {loading && <div className="chat-bubble assistant"><InlineLoading description="Thinking…" /></div>}
      </div>
      <div className="chat-suggestions">
        <button type="button" onClick={() => submit("I have an insulin fridge")}>I have an insulin fridge</button>
        <button type="button" onClick={() => submit("I have an EV")}>I have an EV</button>
        <button type="button" onClick={() => submit("I want lower bills")}>I want lower bills</button>
      </div>
      <form className="chat-form" onSubmit={(event) => { event.preventDefault(); submit(input); }}>
        <label className="sr-only" htmlFor="chat-input">Tell the assistant about your household</label>
        <input id="chat-input" value={input} onChange={(event) => setInput(event.target.value)} placeholder="Tell me about your household…" disabled={loading} />
        <Button type="submit" kind="primary" size="sm" disabled={!input.trim() || loading} renderIcon={Send}>Send</Button>
      </form>
    </Tile>
  );
}

export default function App() {
  // County FIPS — default to Los Angeles for the demo
  const [fips, setFips] = useState("06037");

  // Personal plan state
  const [profile, setProfile] = useState<Profile>(initialProfile);
  const [sources, setSources] = useState<Sources>(() => Object.fromEntries(profileChoices.map((choice) => [choice.key, "sample"])) as Sources);
  const [interviewActive, setInterviewActive] = useState(true);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  // Extra items added by the assistant (from watsonx /v1/plan)
  const [assistantItems, setAssistantItems] = useState<PlanItem[]>([]);

  const basePlan = useMemo(() => createPlan(profile, 50), [profile]);
  // Merge base plan with any assistant-generated items (deduplicate by id)
  const plan = useMemo(() => {
    const existing = new Set(basePlan.map((item) => item.id));
    const extras = assistantItems.filter((item) => !existing.has(item.id));
    return [...basePlan, ...extras];
  }, [basePlan, assistantItems]);

  const toggleProfile = (key: ProfileKey) => { setProfile((current) => ({ ...current, [key]: !current[key] })); setSources((current) => ({ ...current, [key]: "edited" })); };
  const setProfileOn = (key: ProfileKey) => { setProfile((current) => ({ ...current, [key]: true })); setSources((current) => ({ ...current, [key]: "chat" })); };
  const completeInterview = (answer: Profile | null) => { if (answer) { setProfile(answer); setSources(Object.fromEntries(profileChoices.map((choice) => [choice.key, "interview"])) as Sources); } setInterviewActive(false); };

  /** Convert watsonx ApiPlanItems into PlanItems and merge into the plan list. */
  function handleAssistantPlanItems(items: ApiPlanItem[]) {
    const newItems: PlanItem[] = items.map((item, i) => ({
      id: `assistant-${item.when}-${item.title_en.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${i}`,
      when: whenLabel(item.when),
      title: item.title_en,
      why: item.reason_en,
      category: "essential" as const,
    }));
    setAssistantItems((current) => {
      const titles = new Set(current.map((item) => item.title.toLowerCase()));
      return [...current, ...newItems.filter((item) => !titles.has(item.title.toLowerCase()))];
    });
  }

  return (
    <Theme theme="g10">
      <div className="site-shell">
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark"><Flash size={20} /></span>
            <span>runway<span className="brand-period">.</span></span>
          </div>
          <nav className="header-nav" aria-label="Page sections">
            <a href="#forecast">Forecast</a>
            <a href="#plan">My plan</a>
            <a href="#settings">Settings</a>
            <a href="#assistant">Assistant</a>
          </nav>
          <div className="header-right">
            <a className="header-model-link" href="/model-sources">Model sources ↗</a>
            <Tag type="blue" size="sm">Interactive mockup</Tag>
            <span className="avatar" aria-label="Example resident Rosa">R</span>
          </div>
        </header>

        <main>
          <div className="welcome">
            <span className="welcome-date">SAMPLE RESIDENT · ROSA MARTINEZ</span>
            <p>Hello, Rosa. Here is what the next few days may mean for your power.</p>
          </div>

          {/* Forecast section — new API-driven panel */}
          <div id="forecast">
            <ForecastPanel fips={fips} onFipsChange={setFips} />
          </div>

          {/* Interview overlay — only shown before plan is set up */}
          {interviewActive && (
            <div className="interview-overlay">
              <InterviewPanel profile={profile} onComplete={completeInterview} />
            </div>
          )}

          <div className="section-bridge">
            <div>
              <span className="section-kicker">FROM OUTLOOK TO ACTION</span>
              <h2>Your forecast, turned into a plan.</h2>
            </div>
            <ArrowRight size={24} aria-hidden="true" />
          </div>

          <div id="plan">
            <PlanAndSettingsPanel
              profile={profile}
              sources={sources}
              plan={plan}
              checked={checked}
              setChecked={setChecked}
              onToggle={toggleProfile}
              onRestartInterview={() => {
                setInterviewActive(true);
                document.getElementById("forecast")?.scrollIntoView({ behavior: "smooth" });
              }}
            />
          </div>

          <div id="assistant">
            <AssistantPanel profile={profile} onSetProfile={setProfileOn} onPlanItems={handleAssistantPlanItems} />
          </div>
        </main>

        <footer className="site-footer">
          <span>Runway · Static product preview</span>
          <span>
            County forecast data is sample output from an unvalidated model stub.
            No numeric outage probabilities are presented as validated predictions.
          </span>
        </footer>
      </div>
    </Theme>
  );
}
