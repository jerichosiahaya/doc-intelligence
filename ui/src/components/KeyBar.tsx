import { useState, type FormEvent } from "react";
import { setKey } from "../api";

export default function KeyBar({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    setKey(value);
    onSaved();
  }

  return (
    <form className="keybar" onSubmit={submit}>
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="API key"
        aria-label="API key"
        autoComplete="off"
      />
      <button>Save</button>
    </form>
  );
}
