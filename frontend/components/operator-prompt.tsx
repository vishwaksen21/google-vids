"use client";

import { useEffect, useState } from "react";
import { isValidOperator, OPERATOR_PROMPT_EVENT, setOperator, useOperator } from "@/lib/operator";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { TextField } from "./ui/field";

/**
 * Asks for the operator's name the first time someone changes something (an approval, a
 * production control) without having given one. It never opens just because a page loaded.
 */
export function OperatorPrompt() {
  const operator = useOperator();
  const [requested, setRequested] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();

  useEffect(() => {
    const onRequest = () => setRequested(true);
    window.addEventListener(OPERATOR_PROMPT_EVENT, onRequest);
    return () => window.removeEventListener(OPERATOR_PROMPT_EVENT, onRequest);
  }, []);

  const open = operator === null && requested;

  const close = () => setRequested(false);

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValidOperator(name)) {
      setError("Use letters, numbers, spaces and . @ ' - only (up to 80 characters).");
      return;
    }
    setOperator(name);
    setRequested(false);
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="What's your name?"
      description="Approvals and production controls are recorded under your name. You only need to enter it once."
    >
      <form onSubmit={save} className="space-y-5">
        <TextField
          label="Your name"
          name="operator"
          autoComplete="name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(undefined);
          }}
          error={error}
          hint="Shown in the audit trail. This names who acted; it is not a sign-in."
          autoFocus
          maxLength={80}
        />
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={close}>Not now</Button>
          <Button type="submit" variant="primary">
            Save name
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
