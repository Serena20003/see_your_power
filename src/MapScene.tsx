import { layers, type Layer } from "./mockModel";

export default function MapScene({ layer, probability }: { layer: Layer; probability: number }) {
  const hue = { outage: "#ff9d65", wind: "#6aa6eb", heat: "#efb35e", price: "#78c5b1" }[layer];
  const strength = layer === "outage" ? probability / 100 : layer === "wind" ? .72 : layer === "heat" ? .58 : .48;
  return <svg className="map-svg" viewBox="0 0 900 510" preserveAspectRatio="xMidYMid slice" role="img" aria-label={`Illustrative South Bay map centered on Torrance with ${layers.find((item) => item.id === layer)?.label} overlay`}>
    <defs>
      <pattern id="blocks" width="37" height="30" patternUnits="userSpaceOnUse"><path d="M0 0H37M0 0V30" stroke="#d7dedb" strokeWidth="1" opacity=".75" /></pattern>
      <radialGradient id="overlay"><stop offset="0" stopColor={hue} stopOpacity={Math.min(.56, strength * .72)} /><stop offset=".65" stopColor={hue} stopOpacity={Math.min(.28, strength * .4)} /><stop offset="1" stopColor={hue} stopOpacity="0" /></radialGradient>
    </defs>
    <rect width="900" height="510" fill="#edf1ed" /><rect width="900" height="510" fill="url(#blocks)" />
    <path d="M0 0H180C164 58 156 94 164 138C174 198 135 238 119 294C102 347 71 385 0 399Z" fill="#d9eaf0" />
    <path d="M0 399C83 385 103 344 120 292C136 240 174 196 164 138C157 88 166 49 180 0" fill="none" stroke="#b7d2d8" strokeWidth="4" />
    <path d="M119 0C240 66 356 113 486 121S710 105 900 154" className="map-highway" />
    <path d="M0 322C141 280 294 266 439 291S715 326 900 288" className="map-highway" />
    <path d="M338 0C331 98 346 187 335 279S338 410 366 510" className="map-road" />
    <path d="M643 0C627 95 614 177 622 258S680 412 700 510" className="map-road" />
    <path d="M197 0C243 122 253 260 244 510" className="map-road" />
    <path d="M94 445C276 428 480 439 646 408S816 405 900 426" className="map-road" />
    <ellipse cx="478" cy="245" rx="255" ry="173" fill="url(#overlay)" />
    <ellipse cx="632" cy="184" rx="183" ry="143" fill="url(#overlay)" opacity=".7" />
    <ellipse cx="340" cy="366" rx="150" ry="100" fill="url(#overlay)" opacity=".52" />
    <text x="88" y="158" className="map-water-label">PACIFIC OCEAN</text>
    <text x="206" y="91" className="map-label">REDONDO BEACH</text>
    <text x="576" y="85" className="map-label">GARDENA</text>
    <text x="723" y="234" className="map-label">CARSON</text>
    <text x="365" y="405" className="map-label">PALOS VERDES</text>
    <text x="734" y="367" className="map-label">WILMINGTON</text>
    <text x="524" y="204" className="map-label map-label-strong">TORRANCE</text>
    <g transform="translate(486 257)"><circle r="27" fill="#0f62fe" opacity=".16" /><circle r="13" fill="#fff" /><circle r="8" fill="#0f62fe" /></g>
    <g transform="translate(505 282)"><rect width="99" height="28" fill="#161616" /><text x="10" y="19" fill="#fff" fontSize="12" fontWeight="600">Your area</text></g>
  </svg>;
}
