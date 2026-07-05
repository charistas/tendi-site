(() => {
    document.querySelectorAll("[data-email-user][data-email-domain]").forEach((element) => {
        const email = `${element.dataset.emailUser}@${element.dataset.emailDomain}`;
        const link = document.createElement("a");
        link.href = `mailto:${email}`;
        link.textContent = element.dataset.emailText || email;
        link.className = element.className;

        const label = element.dataset.emailLabel;
        if (label) {
            link.setAttribute("aria-label", label);
        }

        element.replaceWith(link);
    });

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion || !("IntersectionObserver" in window)) {
        return;
    }

    document.documentElement.classList.add("reveal-enabled");
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add("is-visible");
                observer.unobserve(entry.target);
            }
        });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.1 });

    document.querySelectorAll(".reveal").forEach((element) => {
        observer.observe(element);
    });
})();
