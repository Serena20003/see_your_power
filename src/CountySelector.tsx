/**
 * CountySelector.tsx — accessible county FIPS selector.
 *
 * In live mode (VITE_API_BASE_URL set) the list is populated from
 * GET /v1/coverage. In demo mode the built-in list below is used —
 * it matches the DEMO_COUNTIES table in api.ts.
 */
import { useEffect, useState } from "react";
import { Select, SelectItem } from "@carbon/react";
import { fetchCoverage } from "./api";

const DEMO_COUNTIES: { fips: string; label: string }[] = [
  { fips: "06037", label: "Los Angeles County, CA" },
  { fips: "06059", label: "Orange County, CA" },
  { fips: "06065", label: "Riverside County, CA" },
  { fips: "06071", label: "San Bernardino County, CA" },
  { fips: "06073", label: "San Diego County, CA" },
  { fips: "06085", label: "Santa Clara County, CA" },
  { fips: "36061", label: "New York County, NY" },
  { fips: "48201", label: "Harris County, TX" },
  { fips: "17031", label: "Cook County, IL" },
  { fips: "53033", label: "King County, WA" },
  { fips: "12086", label: "Miami-Dade County, FL" },
  { fips: "04013", label: "Maricopa County, AZ" },
];

interface Props {
  value: string;
  onChange: (fips: string) => void;
}

export default function CountySelector({ value, onChange }: Props) {
  const [counties, setCounties] = useState(DEMO_COUNTIES);

  useEffect(() => {
    fetchCoverage().then((data) => {
      if (data.length > 0) {
        setCounties(
          data.map((c) => ({
            fips: c.county_fips,
            label: `${c.county_name} County, ${c.state}`,
          }))
        );
      }
    });
  }, []);

  return (
    <div className="county-selector">
      <Select
        id="county-select"
        labelText="Select a county"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        size="sm"
      >
        {counties.map((c) => (
          <SelectItem key={c.fips} value={c.fips} text={c.label} />
        ))}
      </Select>
    </div>
  );
}
