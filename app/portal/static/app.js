// Client side helper for Telegram QR code linking
document.addEventListener("DOMContentLoaded", () => {
  const linkTgBtn = document.getElementById("btn-link-telegram");
  const tgContainer = document.getElementById("telegram-link-box");

  if (linkTgBtn && tgContainer) {
    linkTgBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      linkTgBtn.disabled = true;
      linkTgBtn.innerText = "Đang tạo liên kết...";

      try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
        const res = await fetch("/connections/telegram/link", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
        });

        if (!res.ok) {
          const err = await res.json();
          alert("Lỗi tạo liên kết Telegram: " + (err.error?.message || "Vui lòng thử lại"));
          linkTgBtn.disabled = false;
          linkTgBtn.innerText = "Tạo mã QR / Link";
          return;
        }

        const data = await res.json();
        tgContainer.innerHTML = `
          <div class="qr-container">
            <p><strong>1. Quét mã QR bằng camera/Telegram:</strong></p>
            <img src="${data.qr_code_data_uri}" alt="Telegram QR Link" />
            <p><strong>2. Hoặc nhấp vào link trực tiếp:</strong></p>
            <a href="${data.link_url}" target="_blank" rel="noopener" class="btn btn-primary">Mở Telegram Bot</a>
            <p class="help-text" id="tg-countdown">Mã liên kết có hiệu lực trong: 10 phút 00 giây</p>
          </div>
        `;
        tgContainer.style.display = "block";

        // Countdown timer 10 mins (600s)
        let timeLeft = 600;
        const countdownElem = document.getElementById("tg-countdown");
        const timer = setInterval(() => {
          timeLeft--;
          if (timeLeft <= 0) {
            clearInterval(timer);
            if (countdownElem) countdownElem.innerText = "Mã liên kết đã hết hạn. Vui lòng tạo lại!";
          } else {
            const mins = Math.floor(timeLeft / 60);
            const secs = timeLeft % 60;
            if (countdownElem) {
              countdownElem.innerText = `Mã liên kết có hiệu lực trong: ${mins} phút ${secs.toString().padStart(2, '0')} giây`;
            }
          }
        }, 1000);

      } catch (err) {
        alert("Có lỗi mạng xảy ra: " + err);
        linkTgBtn.disabled = false;
        linkTgBtn.innerText = "Tạo mã QR / Link";
      }
    });
  }
});
