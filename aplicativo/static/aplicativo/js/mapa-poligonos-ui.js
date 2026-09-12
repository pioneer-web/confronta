(function () {
    function norm(value) {
        return (value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .replace(/\s+/g, " ")
            .trim();
    }

    function all(root, selector) {
        return Array.from(root.querySelectorAll(selector));
    }

    function firstBlockByText(drawer, matcher) {
        const nodes = all(drawer, "section, article, div");
        return nodes.find((node) => matcher(norm(node.innerText || "")));
    }

    function closestCardFromElement(el, drawer) {
        let current = el;
        while (current && current !== drawer) {
            const buttons = current.querySelectorAll("button, a");
            if (buttons.length >= 3) return current;
            current = current.parentElement;
        }
        return null;
    }

    function decoratePolygonDrawer(drawer) {
        const fullText = norm(drawer.innerText || "");
        const isPolygonPanel =
            fullText.includes("poligonos da sessao") ||
            fullText.includes("novo poligono") ||
            fullText.includes("exportar todos em kml");

        drawer.classList.toggle("is-polygons-mode", isPolygonPanel);
        if (!isPolygonPanel) return;

        const hero = firstBlockByText(
            drawer,
            (t) => t.includes("poligonos da sessao") && t.includes("novo poligono")
        );
        if (hero) hero.classList.add("cf-poly-hero");

        const summary = firstBlockByText(
            drawer,
            (t) => t.includes("poligono salvo nesta sessao")
        );
        if (summary) {
            summary.classList.add("cf-poly-summary");
            const strongArea = all(summary, "strong, span, div").find((n) =>
                /ha\b/i.test(n.textContent || "")
            );
            if (strongArea) strongArea.classList.add("is-area");
        }

        const exportBlock = firstBlockByText(
            drawer,
            (t) => t.includes("exportar todos em kml") && t.includes("exportar todos em csv")
        );
        if (exportBlock) exportBlock.classList.add("cf-poly-export-grid");

        const actionNodes = all(drawer, "button, a");

        actionNodes.forEach((node) => {
            const t = norm(node.innerText || node.textContent || "");

            if (t.includes("novo poligono")) {
                node.classList.add("cf-poly-primary-btn");
            } else if (t === "kml" || t === "csv" || t.includes("exportar todos")) {
                node.classList.add("cf-poly-soft-btn");
            } else if (t.includes("excluir")) {
                node.classList.add("cf-poly-danger-btn");
            }
        });

        const kmlOrCsv = actionNodes.filter((node) => {
            const t = norm(node.innerText || node.textContent || "");
            return t === "kml" || t === "csv";
        });

        const seenCards = new Set();

        kmlOrCsv.forEach((node) => {
            const card = closestCardFromElement(node, drawer);
            if (!card || seenCards.has(card)) return;

            const cardText = norm(card.innerText || "");
            if (!(cardText.includes("editar") && cardText.includes("excluir"))) return;

            seenCards.add(card);
            card.classList.add("cf-poly-item");

            const nameField = card.querySelector('input[type="text"], input[type="search"]');
            if (nameField) nameField.classList.add("cf-poly-name-field");

            const buttons = all(card, "button, a");
            const btnGroup = buttons.length ? buttons[0].parentElement : null;
            if (btnGroup) btnGroup.classList.add("cf-poly-actions");

            const footerLinks = firstBlockByText(
                card,
                (t) => t.includes("editar") && t.includes("excluir")
            );
            if (footerLinks) footerLinks.classList.add("cf-poly-links");

            const meta = firstBlockByText(
                card,
                (t) => t.includes("desenhado") || /\bha\b/.test(t)
            );
            if (meta) meta.classList.add("cf-poly-meta");
        });
    }

    function boot() {
        document
            .querySelectorAll(".territorial-tool-drawer")
            .forEach(decoratePolygonDrawer);
    }

    const observer = new MutationObserver(() => boot());

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            boot();
            observer.observe(document.body, { childList: true, subtree: true });
        });
    } else {
        boot();
        observer.observe(document.body, { childList: true, subtree: true });
    }
})();
