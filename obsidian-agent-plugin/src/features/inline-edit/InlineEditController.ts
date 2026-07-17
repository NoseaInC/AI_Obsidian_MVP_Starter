import {StateEffect, StateField, type Extension} from "@codemirror/state";
import {Decoration, EditorView, WidgetType, type DecorationSet} from "@codemirror/view";

export interface InlineEditProposal {
  id: string;
  from: number;
  to: number;
  documentSnapshot: string;
  selectedText: string;
  replacement: string;
  onAccept?: (proposal: InlineEditProposal) => Promise<void> | void;
  onReject?: (proposal: InlineEditProposal) => Promise<void> | void;
  onConflict?: (message: string) => void;
}

export const showInlineEdit = StateEffect.define<InlineEditProposal>();
export const clearInlineEdit = StateEffect.define<string>();

class InlineDiffWidget extends WidgetType {
  constructor(private readonly proposal: InlineEditProposal) { super(); }

  eq(other: InlineDiffWidget): boolean {
    return other.proposal.id === this.proposal.id && other.proposal.replacement === this.proposal.replacement;
  }

  toDOM(view: EditorView): HTMLElement {
    const root = document.createElement("section");
    root.className = "la-inline-edit";
    root.setAttribute("aria-label", "知序选区修改预览");

    const diff = document.createElement("div");
    diff.className = "la-inline-edit__diff";
    const before = document.createElement("pre"); before.className = "la-inline-edit__before"; before.textContent = this.proposal.selectedText;
    const after = document.createElement("pre"); after.className = "la-inline-edit__after"; after.textContent = this.proposal.replacement;
    diff.append(before, after); root.append(diff);

    const actions = document.createElement("div"); actions.className = "la-inline-edit__actions";
    const reject = document.createElement("button"); reject.textContent = "拒绝";
    const accept = document.createElement("button"); accept.textContent = "接受"; accept.className = "mod-cta";
    actions.append(reject, accept); root.append(actions);

    reject.addEventListener("click", () => {
      view.dispatch({effects: clearInlineEdit.of(this.proposal.id)});
      void this.proposal.onReject?.(this.proposal);
    });
    accept.addEventListener("click", () => {
      const currentDocument = view.state.doc.toString();
      const currentSelection = currentDocument.slice(this.proposal.from, this.proposal.to);
      if (currentDocument !== this.proposal.documentSnapshot || currentSelection !== this.proposal.selectedText) {
        this.proposal.onConflict?.("原文已变化，请重新生成。");
        view.dispatch({effects: clearInlineEdit.of(this.proposal.id)});
        return;
      }
      view.dispatch({
        changes: {from: this.proposal.from, to: this.proposal.to, insert: this.proposal.replacement},
        effects: clearInlineEdit.of(this.proposal.id),
      });
      void this.proposal.onAccept?.(this.proposal);
    });
    return root;
  }
}

export const inlineEditState = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, transaction) {
    let next = value.map(transaction.changes);
    for (const effect of transaction.effects) {
      if (effect.is(showInlineEdit)) {
        const proposal = effect.value;
        next = Decoration.set([
          Decoration.widget({widget: new InlineDiffWidget(proposal), block: true, side: 1}).range(proposal.to),
        ]);
      } else if (effect.is(clearInlineEdit)) {
        next = Decoration.none;
      }
    }
    return next;
  },
  provide: field => EditorView.decorations.from(field),
});

export const inlineEditExtension: Extension = [inlineEditState];

export function captureInlineEditSelection(view: EditorView): Pick<InlineEditProposal, "from" | "to" | "documentSnapshot" | "selectedText"> {
  const selection = view.state.selection.main;
  const snapshot = view.state.doc.toString();
  return {
    from: selection.from,
    to: selection.to,
    documentSnapshot: snapshot,
    selectedText: snapshot.slice(selection.from, selection.to),
  };
}
