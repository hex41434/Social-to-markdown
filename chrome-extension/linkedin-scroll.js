(() => {
  "use strict";

  function isVisible(element) {
    return !element.disabled
      && (typeof element.getClientRects !== "function" || element.getClientRects().length > 0);
  }

  function findLoadMoreButton(doc) {
    const known = Array.from(doc.querySelectorAll([
      ".scaffold-finite-scroll__load-button",
      "button.infinite-scroller__show-more-button",
    ].join(", "))).find(isVisible);
    if (known) return known;

    return Array.from(doc.querySelectorAll("button")).find((button) => {
      const label = `${button.innerText || ""} ${button.getAttribute?.("aria-label") || ""}`
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
      return isVisible(button)
        && /(?:show|see|load) more results/.test(label);
    }) || null;
  }

  function findScrollableAncestor(node, doc, win) {
    const readStyle = win.getComputedStyle || globalThis.getComputedStyle;
    for (let parent = node?.parentElement; parent; parent = parent.parentElement) {
      const overflow = readStyle ? readStyle(parent).overflowY : "auto";
      if (/(auto|scroll)/.test(overflow) && parent.scrollHeight > parent.clientHeight + 8) {
        return parent;
      }
    }
    return doc.scrollingElement || doc.documentElement || null;
  }

  function advanceResults(lastCard, doc = document, win = window) {
    const loadMore = findLoadMoreButton(doc);
    if (loadMore) {
      loadMore.click();
      return "button";
    }

    lastCard?.scrollIntoView?.({ block: "end", behavior: "auto" });
    const target = findScrollableAncestor(lastCard, doc, win);
    if (!target) return "none";
    if (target === doc.scrollingElement || target === doc.documentElement) {
      win.scrollTo({ top: target.scrollHeight, behavior: "auto" });
    } else {
      target.scrollTo({ top: target.scrollHeight, behavior: "auto" });
    }
    return "scroll";
  }

  globalThis.SocialSavesLinkedInScroll = {
    advanceResults,
    findLoadMoreButton,
    findScrollableAncestor,
  };
})();
