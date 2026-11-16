// SPDX-FileCopyrightText: Copyright 2020 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstddef>
#include <type_traits>

namespace Common {

/// Ceiled integer division.
template <typename N, typename D>
    requires std::is_integral_v<N> && std::is_unsigned_v<D>
[[nodiscard]] constexpr N DivCeil(N number, D divisor) {
    // (hookshot) without number + divisor - 1, which wrapped to a small result near the type's top (as gpu/vk/compat's copy)
    const D n{static_cast<D>(number)};
    return static_cast<N>(n / divisor + (n % divisor != 0 ? 1 : 0));
}

/// Ceiled integer division with logarithmic divisor in base 2
template <typename N, typename D>
    requires std::is_integral_v<N> && std::is_unsigned_v<D>
[[nodiscard]] constexpr N DivCeilLog2(N value, D alignment_log2) {
    return static_cast<N>((static_cast<D>(value) + (D(1) << alignment_log2) - 1) >> alignment_log2);
}

} // namespace Common
